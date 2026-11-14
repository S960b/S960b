# Unholy Trinity

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** Platypwn 2026
- **Category:** crypto
- **Difficulty:** 2
- **Materials:** `server.py`, Dockerfile, compose; saved solver invocation (solver file itself absent)
- **Flags:** 2 accepted, exact strings masked (`PP{...}`)

Two independent weaknesses in a custom RSA-based login service: a modular password hash and a short-secret identification protocol.

**Core issue:** The server sets `e=17`, generates a ~2674-bit RSA modulus `N`, and stores `hash_pw(pw) = int.from_bytes(pw)^17 mod N`. Registration checks for duplicate hash values; therefore distinct valid fixed-length passwords whose integers are congruent modulo N collide. For the admin account the 19-byte secret `m` is far shorter than N: `m^17 < N`. The server prints the admin's committed `pw_hash` before authentication, so its exact integer 17th root reveals `m`, without factoring N.

See the [full solving notes](solve.md).
