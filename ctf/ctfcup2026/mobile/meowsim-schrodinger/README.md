# MeowSim: Schrodinger Edition

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 100
- **Category:** mobile / Android reversing
- **Task:** https://game.ctfcup.online/tasks/mobile-schrodinger-cat
- **Attachment:** `cat.apk`
- **Flag:** `ctfcup{...}` (masked)

The APK is a small Kotlin app (`ru.ctf.meowsim`) with a SQLite box store, two shared preferences and a flag vault. `QuantumState.canOpenFlagVault()` requires the pet to be alive and the box to report the cat dead and in superposition at the same time, stability at least 90, no measurement, and no tamper flag.

The lab feature `RestoreExperimentActivity` restores the box from a snapshot independently of the observer record, which is what makes that state reachable; `stabilize()` supplies stability 92. `FlagVault` then derives a SHA-256 key from `observerToken | boxSeal | stability | catAlive | catDead | superposition | observed | "meowsim-v1"` and XORs the 23-byte encrypted array with `sha256(key || be32(counter))` blocks.

`scripts/decrypt_flag.py <no args>` reproduces the plaintext from the defaults.
