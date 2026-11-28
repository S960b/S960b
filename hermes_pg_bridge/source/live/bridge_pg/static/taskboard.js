'use strict';
const $ = id => document.getElementById(id);
let csrf = '', shown = [], offset = 0, nextList = null, deliveries = {};
let selectedTask = null, showArchived = false, listGeneration = 0, historyGeneration = 0;
let refreshRunning = false;
const firstPages = new Map();
const CACHE_MS = 15000;
const PAGE_SIZE = 30;

function elem(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = String(text);
  if (cls) e.className = cls;
  return e;
}
// Enrollment metadata is not an ordinary message or execution request.
function renderEnrollmentEvent(event, task) {
  if (!event || typeof event !== 'object' || Array.isArray(event) ||
      Object.keys(event).sort().join(',') !== 'kind,note,task_id' ||
      event.kind !== 'agent_opening_note' || event.task_id !== task ||
      typeof event.note !== 'string' || !event.note.trim() ||
      new TextEncoder().encode(event.note).length > 4096 ||
      !/^task_[0-9a-f]{32}$/.test(task)) return null;
  const card = elem('div', undefined, 'message enrollment-event');
  card.append(elem('strong', 'Начальное сообщение MultiTool · регистрация'),
              elem('pre', event.note));
  return card;
}
function button(label, fn, cls) {
  const b = elem('button', label, cls);
  b.addEventListener('click', fn);
  return b;
}
function actor(name) {
  const label = name === 'mt' ? 'MT' : name === 'chatgpt' ? 'ChatGPT' : '—';
  const cls = name === 'mt' ? 'actor-mt' : name === 'chatgpt' ? 'actor-chatgpt' : 'actor-unknown';
  return elem('span', label, 'actor ' + cls);
}
function direction(from, to) {
  const container = elem('span', undefined, 'direction');
  container.append(actor(from), elem('span', '→', 'arrow'), actor(to));
  return container;
}
function turn(name) {
  const container = elem('div', undefined, 'turn-line');
  container.append(elem('span', 'Следующий ход:', 'turn-label'), actor(name));
  return container;
}

function sinceGit(iso) {
  const stamp = Date.parse(iso || '');
  if (!Number.isFinite(stamp)) return '';
  const minutes = Math.max(0, Math.floor((Date.now() - stamp) / 60000));
  if (minutes < 1) return 'только что';
  if (minutes < 60) return minutes + ' мин назад';
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + ' ч назад';
  return Math.floor(hours / 24) + ' дн назад';
}
function canReturn(last, receipt, detailStatus) {
  // A return is possible only while the latest message delivery is still pending.
  if (!last) return false;
  if (last.kind === 'return' || last.kind === 'close' || last.returned) return false;
  if (detailStatus === 'done' || detailStatus === 'closing') return false;
  if (receipt && receipt.source === 'ok' &&
      (receipt.seen_seq != null || receipt.inflight_seq != null)) return false;
  return true;
}
function canHandBack(last, receipt, detailStatus) {
  // Hand back the turn after delivery without retracting the delivered message.
  if (!last) return false;
  if (last.kind === 'return' || last.kind === 'close' || last.returned) return false;
  if (detailStatus === 'done' || detailStatus === 'closing') return false;
  if (last.from !== 'mt') return false;
  // The contract is "after delivery": the report must be acknowledged as seen.
  return Boolean(receipt && receipt.source === 'ok' && receipt.seen_seq != null);
}
function lastAction(last, receipt) {
  if (!last) return {label: 'Пока нет сообщений', time: '', hint: 'В PostgreSQL нет сообщений'};
  const number = Number(last.seq);
  const seq = Number.isSafeInteger(number) && number >= 0 ? number : null;
  const when = last.created_at_utc || '';
  const age = sinceGit(when);
  const ageNote = age ? ' · ' + age : '';
  if (last.kind === 'return') {
    return {label: 'Ход возвращён' + ageNote, time: when,
      hint: 'Подтверждено PostgreSQL. Вернули последний ход, новое сообщение ещё не обязательно появилось.'};
  }
  if (last.from === 'mt') {
    return {label: (last.kind === 'report' ? 'MT ответил' : 'MT записал') + ageNote, time: when,
      hint: 'Подтверждено PostgreSQL: сообщение MT, seq=' + (seq ?? '?') + '. Время относится к записи в PostgreSQL.'};
  }
  if (last.from === 'chatgpt') {
    if (seq !== null && receipt && receipt.source === 'ok') {
      if (Number.isSafeInteger(receipt.seen_seq) && receipt.seen_seq >= seq) {
        return {label: 'Передано MT', time: '',
          hint: 'MT-inbox подтвердил передачу seq=' + seq + ' в MT-сессию. Начало и завершение выполнения не подтверждены; время передачи не фиксируется.'};
      }
      if (receipt.inflight_seq === seq) {
        return {label: 'Передача не подтверждена', time: '',
          hint: 'MT-inbox содержит незавершённую попытку передачи seq=' + seq + '. Проверь состояние локального агента перед повтором; повторный Git-append запрещён без сверки.'};
      }
    }
    return {label: 'ChatGPT записал' + ageNote, time: when,
      hint: 'Подтверждено только PostgreSQL. Получение сообщения MT не подтверждено' +
        (receipt && receipt.source === 'unavailable' ? ' (локальное состояние MT недоступно).' : '.')};
  }
  return {label: 'Запись в PostgreSQL' + ageNote, time: when,
    hint: 'PostgreSQL содержит событие, но отправитель не распознан.'};
}

function copy(value) {
  const fallback = () => window.prompt('Скопируйте значение:', value);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(value).then(() => {
      $('notice').textContent = 'Скопировано: ' + value;
    }).catch(fallback);
  } else fallback();
}
async function api(url, opts = {}) {
  const response = await fetch(url, {cache: 'no-store', ...opts});
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(data.error || ('HTTP ' + response.status));
  return data;
}
function clearCache() {
  firstPages.clear();
}
function firstPage(task) {
  const item = firstPages.get(task);
  if (item && (item.promise || Date.now() - item.at < CACHE_MS)) {
    return item.promise || Promise.resolve(item.data);
  }
  const entry = {at: Date.now(), data: null, promise: null};
  const request = api('/api/history?task_id=' + encodeURIComponent(task) + '&offset=0&limit=20');
  entry.promise = request.then(data => {
    entry.data = data;
    entry.at = Date.now();
    entry.promise = null;
    return data;
  }).catch(error => {
    if (firstPages.get(task) === entry) firstPages.delete(task);
    throw error;
  });
  firstPages.set(task, entry);
  // A small bounded in-browser cache. The authoritative read is never cached for DELETE.
  if (firstPages.size > 50) firstPages.delete(firstPages.keys().next().value);
  return entry.promise;
}
function hideHistory() {
  historyGeneration += 1;
  selectedTask = null;
  $('detail').hidden = true;
  document.body.style.overflow = '';
}
function showHistory(task) {
  selectedTask = task;
  $('detail').hidden = false;
  document.body.style.overflow = 'hidden';
  $('detail-title').textContent = (shown.find(x => x.task_id === task)?.title || '(без названия)') + ' — ' + task;
  $('copy-task').onclick = () => copy(task);
  $('detail-meta').replaceChildren();
  $('messages').replaceChildren();
  $('history-prev').disabled = true;
  $('history-next').disabled = true;
  $('loading-history').textContent = 'Загружаем историю из PostgreSQL…';
  $('close').focus();
}
async function load(next = offset) {
  const generation = ++listGeneration;
  try {
    const result = await api('/api/dialogs?offset=' + next + '&limit=' + PAGE_SIZE);
    if (generation !== listGeneration) return;
    csrf = result.csrf;
    offset = next;
    nextList = result.next_offset;
    shown = result.dialogs;
    const delivery = result.mt_delivery || {};
    deliveries = delivery;
    const rowDetails = result.row_details || {};
    const tbody = $('rows');
    tbody.replaceChildren();
    const visible = shown.filter(x => showArchived || x.lifecycle === 'active');
    for (const item of visible) {
      const tr = elem('tr'), title = elem('td');
      item._title = elem('div', item.title || '(без названия)', 'title');
      title.append(item._title);
      title.append(elem('code', item.task_id), button('⧉', () => copy(item.task_id), 'copy'));
      const job = elem('td', 'Получаем последнее сообщение…', 'muted');
      const status = elem('td');
      status.append(elem('div', item.lifecycle, item.lifecycle === 'active' ? 'success' : 'warn'));
      status.append(elem('small', 'Создал: ' + (item.initiator || '-')));
      const lastAct = elem('td', 'Проверяем…', 'last-action muted');
      const actions = elem('td');
      actions.append(button('История', () => history(item.task_id, 0), 'history-btn'));
      if (item.lifecycle === 'active') {
        item._returnButton = button('Вернуть ход', () => returnTurn(item), 'history-btn');
        item._returnButton.disabled = true;
        actions.append(item._returnButton);
        if (item.task_id === ownerScope57) {
          item._returnButton.hidden = true;
          actions.append(button('Вернуть ChatGPT', () => ownerTurn57(item, 'chatgpt'), 'history-btn'));
          actions.append(button('Вернуть MT', () => ownerTurn57(item, 'agent'), 'history-btn'));
        }
      }
      if (item.lifecycle === 'active' || item.lifecycle === 'cancelled') {
        actions.append(button('Удалить задачу', () => archive(item), 'danger'));
      }
      tr.append(title, job, status, lastAct, actions);
      tbody.append(tr);
      item._job = job;
      item._status = status;
      item._lastAct = lastAct;
    }
    $('list-prev').disabled = offset === 0;
    $('list-next').disabled = nextList === null;
    const apiBudget = result.github_api || {};
    let budgetLabel = 'PostgreSQL · без лимитов GitHub';
    if (Number.isSafeInteger(apiBudget.remaining) &&
        Number.isSafeInteger(apiBudget.limit)) {
      budgetLabel = 'GitHub API: ' + apiBudget.remaining + ' / ' + apiBudget.limit;
      if (apiBudget.paused) budgetLabel += ' · пауза до сброса квоты';
    }
    $('count').textContent = visible.length + ' задач на странице · ' + budgetLabel;
    $('error').textContent = '';
    $('notice').textContent = 'Обновлено ' + new Date().toLocaleTimeString('ru-RU');
    // First-page data is shared with the History button. Avoid a second
    // лишнее чтение PostgreSQL when a row was just populated.
    for (const item of visible) {
      if (generation !== listGeneration) return;
      try {
        const d = rowDetails[item.task_id];
        if (!d) throw Error('row_details_unavailable');
        item._job.replaceChildren();
        if (!item.title && d.title) {
          item.title = d.title;
          item._title.textContent = d.title;
        }
        const last = d.history[0];
        const action = lastAction(last, delivery[item.task_id]);
        item._lastAct.textContent = action.label;
        item._lastAct.title = action.hint;
        if (item._returnButton) {
          const retract = canReturn(last, delivery[item.task_id], d.status);
          const handback = !retract && canHandBack(last, delivery[item.task_id], d.status);
          item._returnButton.disabled = !(retract || handback);
          item._returnButton.textContent = handback ? 'Передать ход агенту' : 'Вернуть ход';
          const started = delivery[item.task_id] && delivery[item.task_id].inflight_seq != null;
          item._returnButton.title = retract ? '' :
            (handback
              ? 'Сообщение уже доставлено и останется в PostgreSQL. Кнопка вернёт ход агенту, чтобы он дописал новое сообщение.'
              : (started ? 'Ждём подтверждения доставки отчёта — после этого ход можно передать агенту.' : ''));
        }
        if (last) {
          item._job.append(elem('code', last.message_id),
                           button('⧉', () => copy(last.message_id), 'copy'));
          const route = elem('div', undefined, 'turn-line');
          route.append(elem('span', '#' + last.seq, 'muted'),
            last.kind === 'return'
              ? elem('span', 'Владелец вернул ход · ожидаем новую версию', 'muted')
              : direction(last.from, last.to));
          item._job.append(route);
        } else {
          item._job.append(elem('small', 'Пока нет сообщений'));
        }
        item._status.append(turn(d.waiting_for));
      } catch (error) {
        item._job.textContent = 'Недоступно; открой историю';
        item._lastAct.textContent = 'История недоступна';
        item._lastAct.title = 'Не удалось проверить последнее событие в PostgreSQL; проверь доступность authority.';
      }
    }
  } catch (error) {
    $('error').textContent = 'Не удалось прочитать задачи: ' + error.message;
  }
}
async function history(task, ofs = 0) {
  const generation = ++historyGeneration;
  const started = performance.now();
  showHistory(task); // immediately visible; never wait silently on fetch()
  try {
    const d = ofs === 0
      ? await firstPage(task)
      : await api('/api/history?task_id=' + encodeURIComponent(task) +
                  '&offset=' + ofs + '&limit=20');
    if (generation !== historyGeneration || selectedTask !== task) return;
    const selected = shown.find(x => x.task_id === task);
    if (selected && !selected.title && d.title) {
      selected.title = d.title;
      if (selected._title) selected._title.textContent = d.title;
    }
    $('detail-title').textContent = (d.title || selected?.title || '(без названия)') + ' — ' + task;
    const meta = $('detail-meta');
    meta.replaceChildren();
    meta.append(turn(d.waiting_for));
    meta.append(elem('div', 'Сообщений: ' + d.total_messages + ' · состояние: ' + d.status, 'muted'));
    meta.append(elem('code', 'VERSION: ' + d.head));
    $('messages').replaceChildren();
    if (ofs === 0) {
      const enrollment = renderEnrollmentEvent(d.enrollment_event, task);
      if (enrollment) $('messages').append(enrollment);
    }
    for (const m of d.history) {
      const card = elem('div', undefined, 'message');
      const line = elem('div', undefined, 'message-meta');
      line.append(elem('strong', '#' + m.seq),
                  m.kind === 'return'
                    ? elem('span', 'Владелец · возврат хода ' + (m.to === 'mt' ? 'MT' : 'ChatGPT'))
                    : direction(m.from, m.to),
                  elem('small', m.created_at_utc + ' · ' + m.kind));
      const id = elem('div', undefined, 'message-meta');
      id.append(elem('code', 'message_id: ' + m.message_id, 'message-id'),
                button('⧉', () => copy(m.message_id), 'copy'));
      if (m.returned) {
        card.classList.add('withdrawn');
        line.append(elem('small', 'Отозвано · история', 'muted'));
      }
      if (m.kind === 'return') {
        line.append(elem('small', 'Возврат хода владельцем', 'muted'));
      }
      card.append(line, id, elem('pre', m.body));
      $('messages').append(card);
    }
    $('history-prev').disabled = ofs === 0;
    $('history-prev').onclick = () => history(task, Math.max(ofs - 20, 0));
    $('history-next').disabled = d.next_offset === null;
    $('history-next').onclick = () => history(task, d.next_offset);
    const elapsed = ((performance.now() - started) / 1000).toFixed(1);
    $('loading-history').textContent = 'История открыта · ' + elapsed + ' с' +
      (ofs === 0 && elapsed === '0.0' ? ' (из кэша)' : '');
  } catch (error) {
    if (generation !== historyGeneration) return;
    $('loading-history').textContent = 'Историю получить не удалось.';
    $('error').textContent = 'История: ' + error.message;
  }
}
const ownerScope57 = 'task_b6c26aa18220412bafdbf32393f16568';
const ownerStorage57 = 'pg-owner-turn57';
let ownerBusy57 = false;
async function ownerTurn57(item, target) {
  if (ownerBusy57) return;
  ownerBusy57 = true;
  try {
    let req = JSON.parse(localStorage.getItem(ownerStorage57) || 'null');
    if (req && (req.task_id !== item.task_id || req.target !== target)) {
      throw new Error('Сначала подтвердите результат предыдущего запроса передачи хода');
    }
    if (!req) {
      const d = await api('/api/history?task_id=' + encodeURIComponent(item.task_id) + '&offset=0&limit=1');
      if (!window.confirm('Передать ход ' + (target === 'agent' ? 'MT' : 'ChatGPT') +
          '? История сохранится. Выполняющееся задание не будет принудительно остановлено.')) return;
      req = {task_id:item.task_id, head:d.head, target,
             request_id:crypto.randomUUID().replaceAll('-','')};
      localStorage.setItem(ownerStorage57,JSON.stringify(req));
    }
    for (let i=0;i<300;i++) {
      let r;
      try {
        r = await api('/api/owner_turn', {method:'POST',
          headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
          body:JSON.stringify(req)});
      } catch(e) {
        $('notice').textContent='Ответ передачи хода не подтверждён. Повторяем тот же запрос.';
        await new Promise(resolve=>setTimeout(resolve,3000));continue;
      }
      if (r.status==='applied' || r.status==='already_actor') {
        localStorage.removeItem(ownerStorage57);
        clearCache();
        $('notice').textContent='Ход у ' + (target==='agent'?'MT':'ChatGPT');
        await load(offset);return;
      }
      if (r.status==='stale') {
        localStorage.removeItem(ownerStorage57);
        clearCache();
        $('error').textContent='Диалог изменился. Обновите историю; новое задание не отменялось.';
        await load(offset);return;
      }
      if (r.status!=='waiting' && r.status!=='uncertain') throw Error('Неизвестный результат передачи хода');
      $('notice').textContent='Запрос сохранён. Ожидаем безопасную границу исполнения или подтверждение.';
      await new Promise(resolve=>setTimeout(resolve,3000));
    }
    $('notice').textContent='Запрос сохранён. Нажмите ту же кнопку для продолжения проверки.';
  } catch(e) {
    $('error').textContent='Передача хода: ' + e.message;
  } finally {ownerBusy57=false;}
}

async function returnTurn(item) {
  if (item.task_id === ownerScope57) return;
  // Never use a cached HEAD for a destructive or turn-changing action.
  try {
    const d = await api('/api/history?task_id=' + encodeURIComponent(item.task_id) +
                        '&offset=0&limit=1');
    const last = d.history[0];
    const retract = canReturn(last, deliveries[item.task_id], d.status);
    const handback = !retract && canHandBack(last, deliveries[item.task_id], d.status);
    if (!retract && !handback) {
      const r = deliveries[item.task_id];
      throw new Error(r && (r.seen_seq != null || r.inflight_seq != null)
        ? 'сообщение уже доставлено (' + (r.inflight_seq != null ? 'доставка идёт' : 'delivered') +
          ', seq=' + (r.inflight_seq != null ? r.inflight_seq : r.seen_seq) +
          ') — передать ход агенту можно только после его сообщения'
        : 'последнее сообщение уже отозвано или диалог завершён');
    }
    if (handback) {
      if (!window.confirm('Передать ход агенту?\n\nСообщение #' + last.seq +
            ' (' + last.message_id + ') останется в PostgreSQL. ' +
            'Агент сможет дописать новое сообщение, ChatGPT прочитает его позже.')) return;
      const handed = await api('/api/hand_back', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
        body: JSON.stringify({task_id: item.task_id, head: d.head})
      });
      if (handed.changed === false) {
        clearCache();
        $('notice').textContent = 'Ход уже у агента — изменений нет';
        await load(offset);
        if (selectedTask === item.task_id) history(item.task_id, 0);
        return;
      }
      if (handed.next_actor !== 'mt' || handed.changed !== true)
        throw new Error('Передача хода не подтверждена');
      clearCache();
      $('notice').textContent = 'Ход передан агенту';
      await load(offset);
      if (selectedTask === item.task_id) history(item.task_id, 0);
      return;
    }
    if (!window.confirm('Вернуть ход ' + (last.from === 'mt' ? 'MT' : 'ChatGPT') +
          '?\n\nСообщение #' + last.seq + ' (' + last.message_id + ')' +
          ' будет отозвано. Уже выполненные действия не отменяются.')) return;
    const response = await api('/api/return_turn', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
      body: JSON.stringify({task_id: item.task_id, head: d.head,
                            last_message_id: last.message_id})
    });
    if (response.returned_message_id !== last.message_id ||
        response.created !== true) throw new Error('Возврат хода не подтверждён');
    clearCache();
    $('notice').textContent = 'Ход возвращён ' + (last.from === 'mt' ? 'MT' : 'ChatGPT');
    await load(offset);
    if (selectedTask === item.task_id) history(item.task_id, 0);
  } catch (error) {
    clearCache();
    $('error').textContent = 'Возврат не подтверждён. Обнови историю: ' + error.message;
  }
}
async function archive(item) {
  const title = item.title || item.task_id;
  const typed = window.prompt('Удалить из активной таскборды: ' + title +
    '\n\nБудущие публикации в эту задачу будут ЗАБЛОКИРОВАНЫ.' +
    '\nДиалог будет архивирован, история сохранится в PostgreSQL и резервных копиях.' +
    '\n\nДля подтверждения напиши полный task_id:');
  if (typed !== item.task_id) return;
  if (!window.confirm('Архивировать диалог ' + item.task_id + '? Действие необратимо.')) return;
  try {
    // Always fetch fresh head outside cache. Authority checks the ref again.
    const d = await api('/api/history?task_id=' + encodeURIComponent(item.task_id) +
                        '&offset=0&limit=1');
    const options = {method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}};
    const prepared = await api('/api/delete_prepare', {
      ...options, body: JSON.stringify({task_id: item.task_id, head: d.head})});
    // Never retry an ambiguous DELETE with a new nonce or HEAD.
    const response = await api('/api/delete_confirm', {
      ...options, body: JSON.stringify({task_id: item.task_id, head: d.head,
                                       confirmation: prepared.confirmation})});
    if (response.deleted !== true || response.remote_absent !== true)
      throw Error('Deletion receipt not confirmed');
    clearCache();
    $('notice').textContent = 'Диалог архивирован: ' + response.task_id;
    await load(0);
  } catch (error) {
    $('error').textContent = 'Удаление не подтверждено. НЕ повторяй автоматически. ' + error.message;
  }
}
$('refresh').onclick = async () => {
  if (refreshRunning) return;
  refreshRunning = true;
  const control = $('refresh');
  control.disabled = true;
  control.textContent = 'Обновляем…';
  $('notice').textContent = 'Обновляем…';
  try {
    clearCache();
    await load();
  } finally {
    control.textContent = 'Обновить';
    control.disabled = false;
    refreshRunning = false;
  }
};
$('show-archived').onchange = e => {showArchived = e.target.checked; load(0);};
$('list-prev').onclick = () => load(Math.max(0, offset - PAGE_SIZE));
$('list-next').onclick = () => {if (nextList !== null) load(nextList);};
$('close').onclick = hideHistory;
$('detail').addEventListener('click', e => {if (e.target === $('detail')) hideHistory();});
document.addEventListener('keydown', e => {if (e.key === 'Escape' && !$('detail').hidden) hideHistory();});
load(0);
