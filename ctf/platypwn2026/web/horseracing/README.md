# Horse Racing

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Category:** web
- **Difficulty:** 1
- **Materials:** Rust/Axum `src/main.rs`, `Cargo.toml`, HTML client; archived successful-run report
- **Flags:** 1 accepted (`PP{...}`)

A horse-racing betting app begins with ten points and awards VIP access at 500. Bets should close when a race starts, but the check runs before the HTTP body is consumed.

**Root cause:** The Axum route uses `middleware::from_fn_with_state(..., betting_open)`. This middleware acquires a read lock and checks `timer < 30` before calling `next.run(request)`. The handler `bet` parses `Json<BetRequest>` later. A client can send valid POST headers with `Content-Length: 80` and hold back the JSON body. The middleware accepts the request before `timer=30`, then the request waits for the body while the race runs. The app exposes positions via `/api/state` every second, including the current 16 horse positions. A late bet can therefore target the clear leader.

See the [solving path](solve.md).
