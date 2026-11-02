# Traverse: recovering a flag from an iCE40 bitstream

[По-русски](solve.ru.md)

## 0. The shape of the problem

The archive `traverse.zip` held two files: `traverse.bin` (32 220 bytes) and `run.py`. The `.bin` name invites a CPU reversing reflex, but the wrapper says otherwise: it builds a testbench around a hardware configuration. The target is a Lattice iCE40 HX1K bitstream. There are no opcodes here, only look-up tables, flip-flops and routing bits.

So the plan was:

1. Read the wrapper to learn how input reaches the device and which byte is the answer.
2. Turn the bitstream into a gate-level netlist.
3. Recover the state machine behind the answer byte.
4. Walk that machine to the accepting state and submit the string that leads there.

## 1. What the wrapper fixes

`run.py` does the whole toolchain for us: it unpacks the bitstream, converts it to Verilog with a pin constraint file, compiles a UART testbench and drives it at 12 MHz with 115200 8N1. Input is a line terminated by `\n`; output is a single byte. The script also contains the `chip.pcf` pin map, and its example string is a placeholder, not the flag.

The practical detail of that day: the machine had no `sudo` and no IceStorm packages. Both are avoidable:

```bash
apt-get download fpga-icestorm iverilog yosys
dpkg-deb -x fpga-icestorm_*.deb /tmp/gigatool/tools
dpkg-deb -x iverilog_*.deb     /tmp/gigatool/tools
```

Wrappers for `iverilog` and `vvp` that pass `-B` / `-M` to `tools/usr/lib/x86_64-linux-gnu/ivl` make `run.py` work unchanged.

Why this step: without a netlist the bitstream is 32 KB of anonymous bits. With it we get named cells and wires, which is the only practical way to reason about the checker.

## 2. From bits to a netlist

```bash
iceunpack traverse.bin chip.asc
icebox_vlog -s -p chip.pcf chip.asc > chip.v
```

The result is 693 LUTs and 69 flip-flops, with no block RAM in use. A useful sanity check: `icepack chip.asc` reproduces `traverse.bin` byte for byte, so nothing relevant lives outside the configuration memory.

Why this step: the absence of BRAM rules out a stored flag or a look-up table of expected bytes. The check has to be computed by logic and state.

## 3. The trap: every input answers `f`

Running the simulator with any input returns exactly one byte, `0x66`, the letter `f`. That holds for all 256 single characters, for lengths from 1 to 300, and for different inter-byte delays. Only the response time scales with length, which tells us the trigger is `\n`, not the comparison result.

Comparing full traces of all 69 registers for `A\n` and `B\n` shows differences only in the receive registers. Read naively, the design looks input-independent, and this is where a brute-force attempt dies.

Why this step: knowing that the visible output is constant forces us to look for hidden state instead of guessing strings.

## 4. Finding the hidden state

Two register groups matter:

- Received byte: `n54` (bit 0), `n56`, `n58`, `n55`, `n51`, `n57`, `n53`, `n52` (bit 7).
- State byte: `n436`, `n439`, `n440`, `n441`, `n530`, `n531`, `n532`, `n534`, written on every accepted character with enable `n146`, reset value `0xA0`.

The answer byte is built at `n106` (end of line) from three bits `led`, `n117`, `n114`, which come from combinational logic over the current state and the current character:

- `(0, 1, 1)` gives `0x66`, the letter `f`
- `(1, 1, 0)` gives `0x73`, the letter `s`

So the machine does move on every character, but it only reports the answer at the newline, and the reported byte depends on the state reached, not on the text directly. That is why single characters and random noise always end in `f`.

## 5. Model, then search

The netlist is regular enough to parse. A small Python evaluator handles the `assign` expressions with `?:`, `!`, `&` and `|`, and reproduces the combinational part exactly; checking it against Icarus on 2768 ticks gave 100 percent agreement.

With `s' = F(s, c)` available, the rest is graph search:

- start state `0xA0`
- edges: every one of the 256 possible characters
- 139 states are reachable
- exactly one state, `0x7f`, produces `(1, 1, 0)` when the newline arrives

The unique path from `0xA0` to `0x7f` is the accepted string. Breadth-first search over the whole reachable set is exhaustive here, so the answer is not a lucky hit.

Why this step: the state space is 256 states by 256 characters, small enough to explore completely, and the accepting condition is a single state.

## 6. Verification on the original bitstream

The found string was replayed through the untouched `run.py` and `traverse.bin`:

- accepted string - `s` (`0x73`)
- last hex digit changed - `f` (`0x66`)
- last character removed - `f`
- first hex digit changed - `f`
- `hello` - `f`

Any mutation, including a change of length, is rejected, so the string is not a prefix coincidence. The UART line itself carries `0x73` for the correct input and `0x66` otherwise.

The flag is masked in this repository as `ctfcup{...}`; the reproduction is in `scripts/solve_traverse.py`:

```bash
iceunpack traverse.bin chip.asc
icebox_vlog -s -p chip.pcf chip.asc > chip.v
python3 scripts/solve_traverse.py chip.v
```

The script prints the reachable state count and the flag line.

## 7. Dead ends worth remembering

- Searching the bitstream, the ASCII dump and the LUT truth tables for `ctfcup{` found nothing, for every bit order and offset.
- Treating the fabric as a ROM split into eight bit planes failed: the truth tables are ordinary logic functions with popcounts of 1, 3 and 9.
- Forcing the receive registers and brute-forcing input bytes gives exactly the behaviour described above, because the answer is not a comparison.
- The earlier `traverse_analysis_scripts.zip` bundle only reproduces the preliminary recon; it does not contain the FSM search.

## 8. Lessons

- Identify the file format before choosing tools. A bitstream is not a binary in the disassembler sense.
- A constant output does not mean a dead checker; it can mean the result is hidden in state that is only observed at a delimiter.
- When the state space is finite and small, exhaustive search over a faithful model beats guessing.

One paragraph: the challenge is an iCE40 design whose checker reports a constant `f` for every line and only reveals the accepting state at the newline; after rebuilding the netlist with IceStorm and modelling the eight-bit state machine in Python, a breadth-first search over 139 reachable states finds the single accepting state `0x7f`, and the path to it is the flag.
