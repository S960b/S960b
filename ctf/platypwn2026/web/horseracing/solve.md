# Horse Racing: solving notes

[По-русски](solve.ru.md)

## 1. Task

A horse-racing betting app begins with ten points and awards VIP access at 500. Bets should close when a race starts, but the check runs before the HTTP body is consumed.

Materials: Rust/Axum `src/main.rs`, `Cargo.toml`, HTML client; direct Git-bridge run report.

## 2. Analysis

The Axum route uses `middleware::from_fn_with_state(..., betting_open)`. This middleware acquires a read lock and checks `timer < 30` before calling `next.run(request)`. The handler `bet` parses `Json<BetRequest>` later. A client can send valid POST headers with `Content-Length: 80` and hold back the JSON body. The middleware accepts the request before `timer=30`, then the request waits for the body while the race runs. The app exposes positions via `/api/state` every second, including the current 16 horse positions. A late bet can therefore target the clear leader.

## 3. Solving procedure

1. Open connection A and send only `POST /api/bet` headers (including `Content-Length: 80` and `Content-Type: application/json`), without sending the body.
2. On connection B, poll `GET /api/state` until the race is running. The successful agent used criteria `lead >= 50` and `lead - second >= 10` to reduce the chance of the leader changing.
3. Send a JSON body on A like `{\"horse\":N,\"amount\":M}` padded with spaces to exactly 80 bytes. The middleware has already passed, so `bet` accepts it while `timer >= 30`.
4. Let the race finish. Bet the available bank on the apparent leader and repeat on the next race.
5. The recorded progression was `10 -> 20 -> 40 -> 80 -> 160 -> 320 -> 640` in six races, reaching the 500-point VIP condition. Read the flag in `/api/state`.

## 4. Evidence

The source confirms the two processing stages, the mutable timer, positions, betting and VIP threshold. The direct agent report records the stalled-body request, HTTP 200 after the race started and the six-race score progression. The accepted flag appears with a character discrepancy between two reports, so the exact string is intentionally not copied.

**Result:** 1 accepted flag(s), masked in the public repository (`PP{...}`).

## 5. Limits

The original socket program is not present in the archive; the report supplies precise request ordering and thresholds but not a complete packet capture.

## 6. Mitigation

Enforce deadline conditions inside the final handler after consuming the entire request body, under the same synchronized state transition as the bet; alternatively freeze request acceptance timestamps at a trusted layer.
