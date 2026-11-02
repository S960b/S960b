# Burmalda

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 100
- **Category:** reverse / web (Godot HTML5)
- **Task:** https://game.ctfcup.online/tasks/burmalda
- **Attachment:** `Burmalda.pck` (43 760 bytes), downloaded from the instance
- **Flag:** `ctfcup{...}` (masked)

The instance serves a roulette game built with Godot 4.4.1 and exported to HTML5. Because the whole game runs in the browser, the resource package and the compiled scripts are delivered to every player.

`Burmalda.pck` starts with `GDPC` and holds 18 entries, including `main.gdc`. Recovering the project with GDRE Tools (`gdrerecover --headless`) yields `main.gd`, where `_decode_flag()` reconstructs the flag from a 151-byte `_EF` array with a 32-bit LCG: state starts at `_SA ^ _SB` (`0xCAFEBABE ^ 0x13371337`), advances with `1664525 * state + 1013904223`, the key byte is `(state >> 13) & 0xff`, and each output byte is `((_EF[i] ^ key) - i) & 0xff`.

`scripts/decode_flag.py` reproduces the routine. The package served by the live instance is byte-identical to the analysed one, so the decoded string is the flag of that deployment.
