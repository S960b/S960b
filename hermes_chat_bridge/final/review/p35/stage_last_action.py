#!/usr/bin/env python3
"""P35: stage the Last action column in a new directory, never edit input."""
import argparse
import hashlib
from pathlib import Path

EXPECTED = {
    "taskboard.py": "15f9d6f657880e4232852ad0a7f30e713b1d4535e6bb7dcdb1b6e6b0e215f19c",
    "taskboard.js": "68c40d21b38608db031a67ff44d9a028afccda0830c9d4620fa6e1ea90b6da28",
    "taskboard.html": "f3a9bfd1a3ec3b47afb2e49a7ff0661adc268bb1c2593ddd0dc16a96d1d40e2a",
}

def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("source layout differs: " + before[:60])
    return text.replace(before, after, 1)

PY_HELPER = '''
MT_STATE = Path("/home/kali/.local/state/mt-git-inbox/state.json")


def mt_delivery_snapshot(task_ids, path=None):
    """Sanitized read-only MT inbox state. 'inflight' is NOT a receipt."""
    import os
    import stat
    source = MT_STATE if path is None else Path(path)
    task_ids = [task for task in task_ids if type(task) is str]
    unavailable = {task: {"source": "unavailable", "seen_seq": None,
                          "inflight_seq": None} for task in task_ids}
    fd = None
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                st.st_nlink != 1 or st.st_mode & 0o022 or st.st_size > 1048576):
            return unavailable
        raw = os.read(fd, 1048577)
        if len(raw) > 1048576:
            return unavailable
        data = json.loads(raw.decode("utf-8"))
        if (type(data) is not dict or type(data.get("seen")) is not dict or
                type(data.get("inflight")) is not dict):
            return unavailable
    except (OSError, ValueError, UnicodeError):
        return unavailable
    finally:
        if fd is not None:
            os.close(fd)

    def checked_seq(value):
        return value if type(value) is int and 0 <= value <= 1000000000 else None

    result = {}
    for task in task_ids:
        pending = data["inflight"].get(task)
        result[task] = {
            "source": "ok",
            "seen_seq": checked_seq(data["seen"].get(task)),
            "inflight_seq": checked_seq(pending.get("seq")) if type(pending) is dict else None,
        }
    return result


'''

JS_HELPERS = '''
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
function lastAction(last, receipt) {
  if (!last) return {label: 'Пока нет сообщений', time: '', hint: 'В Git нет сообщений'};
  const number = Number(last.seq);
  const seq = Number.isSafeInteger(number) && number >= 0 ? number : null;
  const when = last.created_at_utc || '';
  const age = sinceGit(when);
  const ageNote = age ? ' · ' + age : '';
  if (last.kind === 'return') {
    return {label: 'Ход возвращён' + ageNote, time: when,
      hint: 'Подтверждено Git. Вернули последний ход, новое сообщение ещё не обязательно появилось.'};
  }
  if (last.from === 'mt') {
    return {label: (last.kind === 'report' ? 'MT ответил' : 'MT записал') + ageNote, time: when,
      hint: 'Подтверждено Git: сообщение MT, seq=' + (seq ?? '?') + '. Время относится к записи в Git.'};
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
      hint: 'Подтверждено только Git. Получение сообщения MT не подтверждено' +
        (receipt && receipt.source === 'unavailable' ? ' (локальное состояние MT недоступно).' : '.')};
  }
  return {label: 'Запись в Git' + ageNote, time: when,
    hint: 'Git содержит событие, но отправитель не распознан.'};
}
'''

def transform_python(text):
    text = replace_once(text, '\n\nclass Taskboard:\n', '\n\n' + PY_HELPER + 'class Taskboard:\n')
    anchor = '                    response["csrf"] = self.csrf\n'
    text = replace_once(text, anchor, anchor +
        '                    # Read the local MT receipt once per page; never copy raw sessions.\n'
        '                    response["mt_delivery"] = mt_delivery_snapshot(\n'
        '                        [entry["task_id"] for entry in response["dialogs"]])\n')
    import ast
    ast.parse(text)
    return text

def transform_js(text):
    text = replace_once(text, 'function copy(value) {\n', JS_HELPERS + '\nfunction copy(value) {\n')
    text = replace_once(text, '    shown = result.dialogs;\n',
        '    shown = result.dialogs;\n    const delivery = result.mt_delivery || {};\n')
    text = replace_once(text, "      const actions = elem('td');\n",
        "      const lastAct = elem('td', 'Проверяем…', 'last-action muted');\n"
        "      const actions = elem('td');\n")
    text = replace_once(text, '      tr.append(title, job, status, actions);\n',
        '      tr.append(title, job, status, lastAct, actions);\n')
    text = replace_once(text, '      item._status = status;\n',
        '      item._status = status;\n      item._lastAct = lastAct;\n')
    text = replace_once(text, '        const last = d.history[0];\n',
        '        const last = d.history[0];\n'
        '        const action = lastAction(last, delivery[item.task_id]);\n'
        '        item._lastAct.textContent = action.label;\n'
        '        item._lastAct.title = action.hint;\n')
    text = replace_once(text, "        item._job.textContent = 'Недоступно; открой историю';\n",
        "        item._job.textContent = 'Недоступно; открой историю';\n"
        "        item._lastAct.textContent = 'История недоступна';\n"
        "        item._lastAct.title = 'Не удалось проверить последнее Git-событие; проверь доступность authority.';\n")
    return text

def transform_html(text):
    return replace_once(text, '<th>Ход / состояние</th><th>Действия</th>',
                        '<th>Ход / состояние</th><th>Последнее действие</th><th>Действия</th>')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path, help="read-only live git_dialog directory")
    parser.add_argument("output_dir", type=Path, help="new empty staging directory")
    args = parser.parse_args()
    src, out = args.source_dir.resolve(), args.output_dir.resolve()
    if out.exists() or src == out or src in out.parents:
        parser.error("staging output must be a new external directory")
    inputs = {}
    for name, digest in EXPECTED.items():
        raw = (src / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            parser.error("SOURCE_SHA_MISMATCH: " + name)
        inputs[name] = raw.decode("utf-8")
    outputs = {"taskboard.py": transform_python(inputs["taskboard.py"]),
               "taskboard.js": transform_js(inputs["taskboard.js"]),
               "taskboard.html": transform_html(inputs["taskboard.html"])}
    out.mkdir(parents=True, mode=0o700)
    for name, contents in outputs.items():
        with (out / name).open("x", encoding="utf-8", newline="") as fh:
            fh.write(contents)
        print(name + "_sha256=" + hashlib.sha256(contents.encode()).hexdigest())
    print("P35_STAGED_OK")

if __name__ == "__main__":
    main()
