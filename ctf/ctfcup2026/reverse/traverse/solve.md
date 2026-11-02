# Traverse: unpacking an iCE40 FPGA challenge

[По-русски](solve.ru.md)

## 0. What made this different

The challenge package `traverse.zip` contained a binary named `traverse.bin` and a Python wrapper `run.py`. The mistake would have been to approach the binary as an ELF or try x86 disassemblers. The target was an iCE40 FPGA configuration. Its useful representation is a programmable logic fabric with logic cells, local connections and routing bits.

The objective was to reconstruct the behavior implied by that configuration and its Python interface, then recover the flag.

## 1. Read the wrapper before the bitstream

First unpack the attachment and read the Python script:

```bash
unzip -l traverse.zip
unzip traverse.zip -d traverse-work
sed -n '1,240p' traverse-work/run.py
file traverse-work/traverse.bin
```

A wrapper is important because it tells us which inputs reach the hardware model and which outputs matter. The preserved handoff references `parse.py` and `unpack_python.py` for studying the binary data; those names are from the research session, not reconstructed original task files.

## 2. Convert the bitstream to a structural format

The IceStorm toolchain has a utility `iceunpack` for turning iCE40 configuration bits into an ASCII representation. With the appropriate toolchain installed:

```bash
iceunpack traverse-work/traverse.bin > chip.asc
icebox_explain chip.asc > chip.explain.txt
```

The first output, `chip.asc`, is not a Verilog source file. It describes the FPGA configuration, including tile bits. The second output helps identify decoded features. A precise reconstruction requires looking at routing and logic configuration together.

The investigation generated an `active.txt` inventory that narrowed the relevant structure to approximately **692 active logic cells** on an **iCE40 HX1K**. It also reported **no active block RAM**.

The absence of block RAM matters. It makes a design whose central secret lives in BRAM less likely, though it does not exclude constants held in LUT truth tables or logic paths.

## 3. How to reverse the fabric

A useful manual process is to select observable output pins and trace backward through connected resources. For each relevant LUT:

1. Identify its input signals.
2. Read its programmed truth table.
3. Determine which input combinations select each output.
4. Identify flip-flops and feedback that add state.
5. Trace registers from `run.py` input boundaries to output pins.
6. Combine small functions into a higher-level state machine or datapath.

This is different from disassembling instructions: a LUT does not have an opcode or a program counter. The structural netlist is the program.

The original analysis archive reportedly contained `parse.py`, `unpack_python.py`, `chip.asc`, `active.txt`, `run.py` and `traverse.bin`. Those would be the exact resources needed to finish an annotated schematic.

## 4. What we can and cannot reproduce

The historical scoreboard shows Traverse accepted for 207 points, and the user explicitly confirmed the solve. The preserved transcript establishes the FPGA architecture work, extracted active cell inventory and generated analysis artifacts.

It **does not contain** the final recovered bit sequence, the exact operation that maps the FPGA behavior to a flag, or the accepted flag bytes. Publishing a random-looking string or a fabricated simulation transcript here would make the writeup less useful.

The reproducible outcome supported by the surviving data is the bitstream unpacking and circuit-analysis methodology. The final reproduction should be extended only after locating the original `traverse_analysis_scripts.zip` and its solved-session notes.

## 5. Lesson

First recognize the file format. For FPGA problems, source-level tools are often ineffective until the configuration is translated into a logic-level view. After that, reduce the fabric to the active cells and follow the particular signal paths used by the wrapper instead of attempting to understand every bit of the entire device.
