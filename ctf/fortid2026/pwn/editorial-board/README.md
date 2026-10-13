# Editorial Board - ASCII shellcode hidden in valid English prose

- **Platform:** FortiD CTF 2026
- **Category:** Pwn / Binary Exploitation (i386)
- **Author:** (not recorded in the workspace)
- **Points / Solves:** (not recorded in the workspace)
- **Date:** 2026-10-13
- **Remote:** `ncat --ssl <host>:1337` (TLS; the platform gives an instance)
- **Flag:** `FortID{...}` (masked)
- **Artifact:** `handout(6).zip` - `editorial_board` (32-bit ELF, stripped) + `validate.py`

## What the task is

The binary asks for one English "manuscript" (<=900 bytes), validates it with `validate.py` (a LanguageTool-based "anti-AI-slop" grader), and if the text is accepted, copies it into an RWX region and then into a stack buffer via an unchecked `rep movsb` (`sub esp,0x259`, offset to saved EIP = **605**).

So the payload must be at the same time:

1. grammatical English prose that passes the grader (score >= 88, 0 spelling errors),
2. a working exploit made only of `A-Za-z` and ` .,;:'-?!`.

Hence the whole trick: the exploit is written as **self-modifying printable-ASCII i386 shellcode embedded in a meaningful paragraph**.

Two key findings:

- **The CS-prefix bug.** One of the self-patching stores decoded as `2e 00 41 xx` = `add byte ptr cs:[ecx+xx], al`. On native x86 a write through `CS:` raises `#GP`, but `qemu-i386` does not check that - so the exploit "worked" locally and silently died on the server. Fix: drop the CS prefix and compensate the address with two `inc ecx`.
- **The editorial requirements.** A comma in the prologue caused `COMMA_PARENTHESIS_WHITESPACE`; glued tokens like `.RABPAX` caused `MORFOLOGIK_RULE_EN_US`. The comma-free prologue ` AJ AR AZ-Sugar:` plus dot-split short tokens `.R.A.BA` passed: score **89.45**, spelling 0, issues 4 - **ACCEPT**.

The exploit is two-staged: stage 1 does `read(0, EIP+6, 115)`, stage 2 (115 raw bytes) is the ORW `open("/flag") - read - write`.

The full analysis is in [`solve.md`](solve.md).

## Folder structure

```text
pwn/editorial-board/
├── README.md          # this file: the task context
├── solve.md           # the solution: analysis, pitfalls, scripts, flag
├── assets/            # from the handout
│   ├── editorial_board   # 32-bit ELF, stripped
│   └── validate.py       # the original grader
├── scripts/           # our scripts
│   ├── editorial_stage9_shorttokens.py    # final: manuscript + stage 2 + send
│   ├── editorial_stage9_commafree.py      # previous iteration, ORW builder
│   ├── editorial_native_grammar_repair.py # CS-free fix, prints a marker
│   ├── editorial_native_cs_fix.py
│   └── editorial_board_live_solver.py     # diagnostics / timings
└── evidence/          # raw artifacts of the run
    ├── baseline_stage8_accepted_899.txt   # accepted base used for patching
    ├── manuscript_accepted_899.txt        # the accepted manuscript (899 B)
    ├── stage2_marker.bin                  # stage 2: the marker
    ├── remote_marker_probe.raw            # server reply with the marker
    └── remote_flag_orw.raw                # server reply with the flag
```

## Reproduction

The attack can be fully repeated only on a live instance (closed after the event), but the manuscript and payload builds can be checked offline - `build()` is deterministic:

```bash
# final run (needs a live host, LanguageTool and validate.py next to it)
PATH="$PWD/bin_shim:$PATH" ./venv/bin/python scripts/editorial_stage9_shorttokens.py \
  --baseline evidence/baseline_stage8_accepted_899.txt \
  --host editorial-board-<INSTANCE>.inst2.ctf.fortid.com --limit 400 --deadline 85
```

Verified offline (byte-exact): `build(baseline, 'Sugar', 'rain', b'.R.A.BA')` reproduces `evidence/manuscript_accepted_899.txt`, and `marker_stage2()` reproduces `evidence/stage2_marker.bin`. In both files `message[605:609] = b"able"` - EIP = `0x656c6261` inside the RWX region (see `solve.md`).

## What is NOT in the folder

`venv/` with `language_tool_python` and the Java LanguageTool wrapper (tens of megabytes) - it is only needed for a full run, not for reading the solution. The instance is dead, so a full end-to-end is not recorded here; the proof is in `evidence/*.raw`.