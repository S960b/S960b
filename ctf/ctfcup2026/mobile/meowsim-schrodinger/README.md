# MeowSim: Schrodinger Edition

[Русская версия](README.ru.md) | [Full solve](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 100
- **Category:** mobile, Android reversing
- **Challenge page:** https://game.ctfcup.online/tasks/mobile-schrodinger-cat
- **Attachment:** `cat.apk`
- **Flag:** `ctfcup{...}` (masked)

A deliberately contradictory virtual cat can be both alive and dead. The Android application has a hidden vault guarded by state predicates. The solve was static: inspect `classes.dex`, identify the gate in `QuantumState`, then reconstruct how `FlagVault` decrypts the embedded bytes. No Android emulator was needed.

The exact original APK is not republished here. Read [solve.md](solve.md) for the method and the confirmed final state.
