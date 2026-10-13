# One Last Review - use-after-free in a heap "leave a review" menu

- **Platform:** FortiD CTF 2026
- **Category:** Pwn (heap, baby-pwn)
- **Author:** (not recorded in the workspace)
- **Points / Solves:** (not recorded in the workspace)
- **Date:** 2026-10-13
- **Remote:** `ncat --ssl <host>:1337` (TLS; the platform gives an instance)
- **Flag:** `FortID{...}` (masked)
- **Artifact:** `handout(2).zip` - `chall.c` + `chall` (ELF64, not PIE, not stripped)

## What the task is

A console menu "leave a review" with 6 options:

```text
1. Submit a review
2. Mark review as favourite
3. Delete review
4. Write a draft
5. Preview favourite review
6. Leave
```

`win()` exists in the binary (and is not optimized out by the compiler); it opens `flag.txt` and prints it, but nothing in the menu calls it - so we have to hijack an indirect call.

The review struct is exactly 40 bytes: `char text[32]` + a function pointer `display`. `delete_review()` frees the chunk and nulls `review`, but **does not touch `favourite`** - a dangling pointer (UAF). `write_draft()` allocates a chunk of the same size (`draft_t.contents[sizeof(review_t)]`) and fills it with our 40 bytes - i.e. it reuses the freed chunk. `preview_favourite()` calls the `display` pointer of the favourite review - we swap it for `win` and get the flag.

The full analysis is in [`solve.md`](solve.md).

## Folder structure

```text
pwn/one-last-review/
├── README.md          # this file: the task context
├── solve.md           # the solution: analysis, exploit, flag
├── assets/
│   ├── chall.c        # the challenge source
│   └── chall          # ELF64, non-PIE (win = 0x40135f, not stripped)
└── scripts/
    └── solve.py       # exploit: <host> [port] or --local
```

## Reproduction

```bash
python3 scripts/solve.py --local                       # local check against ./assets/chall
python3 scripts/solve.py <host> 1337                   # the real instance (TLS)
```

The original challenge archive stayed in `~/Downloads/handout(2).zip`; the folder contains the unpacked copy.