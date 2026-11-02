# Traverse

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 207
- **Category:** reverse engineering / FPGA
- **Task:** https://game.ctfcup.online/tasks/traverse
- **Attachments:** `traverse.zip`, with `traverse.bin` and `run.py`
- **Flag:** `ctfcup{...}` (masked)

The attachment was an iCE40 HX1K configuration, not a CPU executable. Unpacking it with IceStorm and converting it to a netlist gives 693 LUTs and 69 flip-flops with no block RAM, and `icepack` reproduces the original bitstream byte for byte.

The design answers a single byte for every input line and always says `f` (`0x66`). The real result appears only at the newline and depends on an eight-bit state register that is updated on every character. A Python model of the combinational logic, checked against Icarus on 2768 ticks, plus a breadth-first search over the 139 reachable states finds exactly one accepting state (`0x7f`), and the path to it is the flag. Replaying it through the original `run.py` returns `s` (`0x73`), and every mutation returns `f`.

The solver is in `scripts/solve_traverse.py`.
