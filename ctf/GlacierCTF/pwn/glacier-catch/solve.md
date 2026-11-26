# Glacier Catch — solution

[Русская версия](solve.ru.md)

## 1. Environment and attack surface

The supplied `glacier-catch.tar.gz` contains a static PIE x86-64 `chip8` interpreter running on Alpine Linux under SDL2/Xvfb. Its web front-end is Flask (`src/app.py`). `POST /api/start` uploads a custom ROM (maximum `0xE00` bytes), `POST /api/keys` supplies key events (optionally buffered directly to stdin), `GET /api/status` reports whether the emulator is alive and `GET /stream` returns JPEG frames downsized to **64×32**.

There is no need to compromise Flask itself. The ROM is attacker-controlled and the bug is in the *native emulator*, whose process can read `/flag.txt`. The task container has SDL2 2.30.9 and musl 1.2.5 (the environment matters for hardcoded offsets).

The remote launcher originally provided a per-instance HTTPS URL. Those instances have expired. We use placeholders rather than a historical instance ID.

## 2. Native-memory access via CHIP-8 instructions

CHIP-8 has only 4096 bytes of addressable RAM. However, the emulator's implementation of `FX65` (load V0..VX from memory at I) and `FX55` (store V0..VX at I) allowed the 16-bit I register to address data outside that RAM.

The virtual memory starts at **`Cpu + 0x866`**. Thus, a native object field at offset `O` can be targeted with `I = O - 0x866`. The renderer pointer itself is at `Cpu + 0x1868`, reached with `I=0x1002`. This is a native OOB read/write primitive, not a guest ROM bounds-check feature.

The ROM draws leaked native words into the CHIP-8 framebuffer; the HTTP client decodes pixel brightness from `/stream` to recover bytes. Conversely, a 536-byte payload is sent as pairs of keypad nibbles, then written into the virtual RAM via `FX55`.

## 3. Leaks and runtime layout

The crucial address relationship on the successful remote setup was:

```text
SDL_Renderer *renderer = *(Cpu + 0x1868)
renderer - Cpu = 0x58f0
Cpu RAM = Cpu + 0x866
```

The value **`0x58f0`** was found by an in-ROM scanner and rechecked using three leaks in one process. It was *not* stable across environments: local probes saw `0x1930`, `0x2be0` and `0x12fb0`. A hardcoded delta from the author's machine therefore broke the first remote attempts.

The three leak stages yield: the `SDL_Renderer*` at `Cpu+0x1868`; a renderer magic/function-related pointer used to calculate PIE; and the `SDL_RenderPresent` callback at `renderer+0xb0`. Their validation conditions were:

```python
pie = magic - 0x484e44
assert pie & 0xfff == 0
assert callback == pie + 0x6c88f
```

The first two constraints distinguish a genuine pointer from stale/malformed framebuffer reads. The successful marker transcript included the expected relationship and `delta=0x58f0`.

## 4. Why direct renderer patching failed

The initial ROM tried to overwrite renderer internals in-place, patch `+0x30`/`+0x38` for `_longjmp` state and replace the callback at `+0xb0`. This regularly terminated the process: `/stream` returned **HTTP 409**.

We did not assume that the payload was lost. An integrity ROM verified delivery of all **536 bytes**, encoded as **2144 key events**, and eight framebuffer checks. Checkpoint ROMs showed that staged field writes themselves could succeed; crashes happened during or after the native callback/ROP transition.

A fastpatch experiment with ROM padding 1, 33, 65, 97, 129, 161, 193 and 225 bytes did not avoid the crash. Local allocator layouts also differed significantly from the remote one.

The exact fault instruction for the unsuccessful versions was not retained, so the idea that the live SDL callback had become inconsistent is a *working explanation*, not independently proven root cause.

## 5. Final primitive: clone, patch, swap

The successful exploit, `glacier_catch_clone_renderer_patched.py`, copied **0x300 bytes** from the live `SDL_Renderer` into virtual RAM at **`0x800..0xaff`** (32 chunks of 8 bytes for each of three 256-byte pages). The actual native address of the copy is `Cpu + 0x866 + 0x800`.

It then modified **only the copy**, using payload-header bytes already loaded at VM `0x400`:

| Renderer field | Size | Purpose |
| --- | ---: | --- |
| `+0x30` | 8 bytes | restored/jump stack pointer for `_longjmp` |
| `+0x38` | 8 bytes | execution target |
| `+0x68` | 8 bytes | callback-related state |
| `+0xb0` | 8 bytes | change render-present callback to `_longjmp` |
| `+0x284` | 4 bytes | renderer state |
| `+0x2cc` | 1 byte | renderer state |

Crucially, **the real renderer remains intact** while the ROM prepares the fake one. When everything is ready, the ROM loads the fake pointer and executes one OOB store with `I=0x1002`, replacing `Cpu.renderer`. Only then does `00E0` trigger the fake callback and its `_longjmp` path.

The key order is **copy → patch copy → swap one pointer → trigger**, rather than modifying a live object while the UI is rendering.

## 6. ROP chain and return to CHIP-8

The script uses leaked PIE to resolve native gadgets and function addresses. Selected offsets from the preserved working source:

```text
_longjmp                PIE+0x312f90
memcpy                  PIE+0x318891
open                    PIE+0x307c67
read                    PIE+0x31beaf
Cpu::run                PIE+0x27e0c
pop rdi / pop rsi / pop rdx
                         PIE+0x25278 / PIE+0x24f18 / PIE+0x23ae97
xchg eax,edi; ret       PIE+0x10b2b7
```

The payload layout is **216 bytes at virtual `0x400` + 320 bytes at `0xe02`** for a total of 536. The native fake stack is `Cpu + 0x866 + 0xe02`, aligned as expected by the caller.

Two modes separate execution control from file access:

1. **`marker`**: use a short ROP `memcpy` to place `ROP_OK!!` at VM `0x600`, restore `Cpu.renderer` and re-enter `Cpu::run`. The remote stream displayed the marker. This validated the complete native control-flow round trip.
2. **`read-dynamic`**: `open("/flag.txt", 0)` → move the returned descriptor from EAX to EDI with `xchg eax,edi; ret` → `read(fd, VM+0x600, 128)` → restore `Cpu.renderer` with `memcpy` → resume `Cpu::run`.

The `open` return is *not* assumed to be descriptor 3. The script dynamically forwards it to the read call. It does not require a shell, privilege escalation or container escape.

After returning, the ROM draws RAM bytes as 8-pixel-wide bitmap rows across the 64×32 display. The final build uses **eight 15-byte blocks plus a final 8-byte block**, yielding room to display up to 128 bytes. In the previous 64-byte version the final flag was cut off: the recovered flag is **68 bytes** long.

## 7. Output and evidence

The recorded successful marker attempt ended:

```text
[FAKE] mode=marker ... ROP=18 qwords
[keys] 2144/2144 events
[+] STAGE PASSED: ROP_OK!!
```

A separate instance in `read-dynamic` mode produced framebuffer image `flag128.png`. The agent decoded the complete `gctf{...}` value. The participant has confirmed acceptance by the GlacierCTF platform. The full flag is deliberately excluded from GitHub.

The historical material includes the full flag in `FLAG.txt`, the successful screenshots, the exact patched exploit and earlier trial/error logs. The raw terminal transcript for the final successful remote run is **not** independently stored; the report quotes its observed marker output and retains screenshots.

## 8. Reproduction from the retained agent bundle

The user's archive `glacierctf_glacier_catch_materials.zip` contains:

```text
glacierctf_glacier_catch/
  REPORT.md
  FLAG.txt
  assets/glacier-catch.tar.gz
  scripts/working/glacier_catch_clone_renderer_patched.py
  scripts/working/glacier_catch_standalone_proxyfixed.py
  evidence/screenshots/flag128.png
  evidence/roms/
  evidence/payloads/
```

The working clone script imports `glacier_catch_standalone` and uses its API. **The dependency in the archive is named `glacier_catch_standalone_proxyfixed.py`**; on a private local copy it must be renamed to `glacier_catch_standalone.py` before use. See [scripts/README.md](scripts/README.md).

Historic invocation (not re-run for this write-up):

```sh
python3 glacier_catch_clone_renderer.py --url 'https://<instance>.glacier-catch.web.glacierctf.com' --delta 0x58f0 --mode marker
python3 glacier_catch_clone_renderer.py --url 'https://<instance>.glacier-catch.web.glacierctf.com' --delta 0x58f0 --mode read-dynamic
```

Each mode was tested on its own fresh instance. `0x58f0` was a **measured property of those instances**, not a portable universal offset. For offline inspection use the ROM/ROP sources and `scripts/decode_frame.py` rather than assuming the remote service still exists.

## 9. Lessons

- Interpreter array indexing must constrain `I` and transferred register count to 4096-byte VM memory.
- Client-controlled ROMs can become native memory corruption primitives even through a harmless-looking arcade frontend.
- Leaked pointers can defeat PIE/ASLR when their offsets are known.
- Patching a cloned callback object and atomically replacing a pointer may be more stable than mutating an actively used object.
- Validate payload delivery, separate a marker chain from the full ORW chain, and size reads large enough for the complete flag.

No new binary execution or network exploitation was performed while preparing this documentation.
