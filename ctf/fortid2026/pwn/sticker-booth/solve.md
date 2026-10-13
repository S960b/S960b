# Sticker Booth - writeup (pwn: refcount confusion, UAF, file read)

## Getting to know the program

Description: "A place where you can safely customize your stickers and print them out!"

The handout is a zip with the binary `chall`, `recipe_docs.md` (a manual), `sample.recipe` and an `assets/` folder with three tiny files (`heart.txt`, `star.txt`, `smile.txt` - each a few bytes).

The program is a "sticker sheet designer". It reads commands from standard input, one per line. From the manual:

- `photo NAME W H RGBA_HEX` - add a photo, pixels are W*H*4 hex bytes (RGBA)
- `caption NAME TEXT` - add a text caption
- `builtin NAME heart|star|smile` - add one of the built-in stickers
- `mono NAME` - apply a monochrome filter to a photo
- `remove NAME` - remove an item
- `upload NAME HEX` - upload a custom sticker as raw hex bytes
- `print` - print the sheet

First I fed the program a trivial recipe (photo + print) to see the output format. Then I started looking at the internals: in binaries like this you find bugs by reading code, not by guessing, so the first step is static analysis.

## How I read the binary (which tools)

- `file chall` - x86-64 ELF, statically linked, NOT PIE, **not stripped**.
  Statically linked - the whole program is in one file, no external libraries. Not PIE - addresses are fixed (useful later with gdb). Not stripped - the symbol names of functions and globals are still in the file, so I could read it by names instead of guessing.
- `nm chall` (or `readelf -s`) - symbol list with addresses. I wrote down the globals whose names look important:

  ```text
  booth_ticket @ 0x4acb20   (8 random bytes from /dev/urandom, read at startup)
  top          @ 0x4acb28   (allocator top)
  nblocks      @ 0x4acb30
  items[]      @ 0x4acb40   (32 slots, 0x28 bytes each)
  blocks[]     @ 0x4ad040   (block metadata, 0x18 each: {offset, size, used})
  pool         @ 0x4ad640   (a 16 KiB pool)
  ```

  Why these matter: `booth_ticket` is a secret by its name (later it turns out to gate the file read); `items`, `blocks`, `pool`, `top` are the memory layout where the program stores everything. Without this map nothing else makes sense.
- `objdump -d -M intel chall` - disassembly. By symbol names I found the command handlers (`photo`, `caption`, `builtin`, `mono`, `remove`, `upload`, `print`) and the memory-allocation function `xalloc` (0x402340) - and read them one by one.
- `strings chall` - useful strings: "[broken sticker]", "assets/heart.txt" etc. That was the first hint that built-in stickers are somehow file-backed.
- gdb - when I wanted to confirm I understood a data structure right, I set breakpoints and dumped memory (examples below).

## Program memory: what an allocator is (in plain words)

The program does not call malloc per object - it has its own allocator, `xalloc`. In BSS there is one big reserved chunk - a **16 KiB pool** (`pool @ 0x4ad640`) - and `xalloc` hands out pieces of it on request.

What a "bump allocator" is: the simplest allocation scheme. Next to the pool there is a counter `top` - how much of the pool has been handed out. A new chunk is "cut off the top": it lands at `pool + top` and `top` moves up by the size. Cheap and fast, but memory is never reused unless something explicitly frees a chunk.

Here `xalloc` is a bit smarter. Next to the pool there is a block list `blocks[]` (0x18 bytes each: offset, size, used). I read `xalloc` in objdump, step by step:

1. round the requested size up to 8;
2. scan `blocks[]`: if there is a block with `used = 0` and `size >= wanted` - reuse it (first fit): mark `used = 1` and **memset the whole pool from that block to the end** (everything after it becomes zeros);
3. if no suitable free block - cut a new chunk off the top (`top += size`) and append a block to the list.

How I verified this instead of just trusting the disassembly: in gdb I freed a block, requested a chunk of the same size - `xalloc` returned the same address, and the bytes after it were zeros. Behavior confirmed.

Why this matters for the attack:
1. **Placement is predictable**: new chunks land consecutively at the top.
2. **A freed block can be re-requested**: by asking for the right size you get exactly the block we freed.
3. **Reuse zeroes the pool tail**: the object's fields get "reset", and we can reshape them with our own bytes (that is phase 2).

## What an item and data are

The program stores objects as **items**: a fixed 0x28-byte struct in `items[]` (in BSS), while the object's actual data lives in the pool and is pointed to by the `data` field (offset +0x20); the object type is the `type` field (+0x18). I found this in objdump: `print` and the create functions access `items[i]+0x18` and `items[i]+0x20`.

Object types:
- **1 - photo**: `[W][H][stride][?][RGBA pixels]`, allocated 0x10 + 4*W*H bytes;
- **2 - caption or built-in sticker**;
- **3 - upload**: arbitrary bytes we choose, max 0x100.

What lives in `data` for type 2. I created `builtin x heart` and dumped the block bytes in gdb (`x/40bx` at the data pointer):

```text
data[0..1]    refcount (for captions; see Bug 2)
data[2]       subtype: 1 = text caption, 2 = sticker-from-file
data[3]       a flag (builtin has 1)
data[4..7]    length (of the text or the path)
data[8..15]   booth_ticket, 8 bytes
data[0x10..]  path to a file, e.g. "assets/heart.txt"
```

The ticket is 8 random bytes read from `/dev/urandom` at startup into `booth_ticket` and kept in BSS.

## The print branches: which is first, which is second

`print` walks all items and prints each one. For type 2 the disassembly of `print` shows a chain of comparisons - three branches:

- **first**: `data[2] = 1` - "caption": print `data[4]` bytes from `data+0x10` (`fwrite`) - just the caption text;
- **second**: `data[2] = 2` AND `data[3]` non-zero AND the 8 bytes at `data+8` equal `booth_ticket` - open the file at `data+0x10` (length `data[4]`) and dump it;
- otherwise - "[broken sticker]".

The second branch is a **legit mechanism**: that is how built-in stickers are printed. The program puts a path like `"assets/heart.txt"` and the ticket into the block, and when `print` meets such an item it opens the file and prints its content (the stickers themselves are files in `assets/`; the paths are visible in `strings`). What this gives us: if I can place my own bytes in `data` that pass the check, with the path `"flag.txt"`, the program itself will open and print the file I want. A file read, effectively LFI (local file inclusion).

The catch: the check requires 8 random ticket bytes we do not know. So first we need a way to leak the ticket, and only then use the second branch. That is exactly the shape: phase 1 leaks the ticket, phase 2 uses it to read the flag.

## Bug 1: the `mono` filter draws one row too many

I was looking for a command that can affect **other** data - the most common bug source in such programs. `photo` and `upload` write exactly what they are asked; `caption` only reads existing data (dedup, see below); but `mono` writes **into an existing photo** - there, where the program computes boundaries, it is worth looking closely.

In objdump the `mono` loop is:

```text
row = 0
loop:
  ... write 4*W bytes into row (zero RGB of each pixel, leave alpha) ...
  row += 1
  cmp row, H
  jae loop        ; jump while row < H, i.e. rows 0..H inclusive are processed
```

What raised my suspicion: the comparison with H comes **after** the increment of `row`, and the loop continues while `row < H`. Since `row` starts at 0 and reaches H inclusively - H+1 rows are processed, not H. A classic off-by-one. (For comparison, a correct loop would check `row < H` before processing, or compare with H-1.)

Testing the hypothesis instead of trusting my eyes: a photo 2x1 (H = 1). Row 0 is legit, but row 1 is already past the photo: it writes 4*W = 8 bytes into the next pool block. In gdb I created a photo, then a caption right after it, ran `mono` - and saw the first bytes of the caption zeroed. Hypothesis confirmed.

The nature of the overwrite:
- **zero-only**: of every 4 bytes (RGBA), three (RGB) become zeros, alpha stays. Arbitrary values cannot be written;
- it lands in the block that follows the photo in the pool;
- at most 4*W bytes past the boundary (W is bounded, so not much).

Looks useless at first - zeros! But zeros are exactly what we need: a refcount is two bytes at the start of a neighbor block. Zeroing a refcount turns a "shared" buffer into a "freeable" one while someone still points at it. That is the bridge to a UAF (use-after-free).

## Bug 2: identical captions share one buffer (refcount)

While reading `caption` I noticed deduplication: if a caption with the same text already exists, the command does NOT allocate a new buffer - it creates a new item pointing at the **same** block and increments the refcount in `data[0..1]`.

Where I learned about the refcount - from `remove`. Its logic:

```text
if data[0..1] != 0: decrement the counter; if it hits 0 - free the block (used = 0) and clear the item slot
if data[0..1] = 0: free the block and clear the slot
```

So a buffer stays alive while the counter is above zero; `remove` frees it only when the counter drops to zero. Two identical captions `a` and `b` - one block with refcount 2; removing one gives refcount 1, the block stays alive.

The danger is that the counter lives in the block data, in the pool, right next to a photo - and `mono` can write zeros into the neighbor block. Two bugs combine into one primitive.

## Putting it together: refcount confusion, UAF, ticket leak

The plan:
1. Create a photo so that `mono`'s extra row lands exactly at the start of the caption block.
2. Zero the refcount - the shared buffer stops being "shared".
3. Remove one caption - the block is freed while the second caption still points at it (dangling pointer, UAF).
4. Re-allocate the block with `upload` (our bytes) and place a `builtin` right after it - then `print` through the dangling caption leaks the memory past the block, including the ticket.

Phase 1 recipe (leak the ticket):

```text
photo p 2 1 0102030405060708   # photo block = 0x10 + 4*2*1 = 24 bytes; mono's row 1
                               # starts exactly at the next block
caption a AAAAAAAAAAAAAAAA     # block D1 = 32 bytes, refcount 2 (a and b)
caption b AAAAAAAAAAAAAAAA
mono p                         # the extra row zeroes D1[0..2]: refcount and data[2] = 0
remove a                       # refcount 0, D1 is freed; b still points at D1
upload c 000001002000000000... # xalloc(32) returns D1; our bytes: data[2]=1, data[4]=0x20
builtin x heart                # the new block lands right after D1: head + ticket + path
print b                        # b: type 2, data[2]=1 - "caption" branch:
                               # fwrite(D1+0x10, 1, 0x20) - dumps 32 bytes
```

Why the photo is exactly 2x1: `mono` writes rows of 4*W bytes. For a 2x1 photo the block is 24 bytes, row 1 starts at offset 24 - exactly at the first byte of the next block D1. And of the 8 row bytes, bytes 0,1,2,4,5,6 get zeroed (RGB positions) - including the refcount `D1[0..1]` and `data[2]`.

What happens at the last `print`:
- `b` is type 2 with `data[2] = 1`, so the "caption" branch fires: `fwrite(D1+0x10, 1, 0x20)` - print 32 bytes starting at byte 16 of D1.
- D1 (32 bytes) is now occupied by upload `c`: the first 8 bytes are ours (00 00 01 00 20 00 00 00), the rest are zeros. Bytes 16..31 of D1 are zeros.
- `builtin x heart` was allocated right after D1, at byte 32: the head `01 00 02 01 10 00 00 00` (8 bytes), then 8 ticket bytes, then the path.
- So the output is: 16 zero bytes, the pattern `01 00 02 01 10 00 00 00`, and right after it 8 bytes - that is the booth_ticket. The script finds the pattern and extracts the ticket.

(The math: `fwrite(D1+0x10, 1, 0x20)` prints bytes 16..47 of D1 - i.e. D1's tail and the start of the next block. The next block is the builtin: an 8-byte head + 8 ticket bytes. The ticket shows up in the output right after the head pattern.)

## Phase 2: rebuild the block as a "sticker-from-file"

We have the ticket. We need a dangling pointer to D1 again - there already is one: `b` still points at D1. Right now D1 is occupied by upload `c`, so:

```text
remove c           # c's data[0..1] = 0, D1 is freed; b still points at D1
upload d <payload> # payload = 00 00 02 01 | 08 00 00 00 | ticket | "flag.txt\0"
print b            # b: data[2]=2, data[3]=1, data[8..15] = ticket -
                   # the check passes, the second branch fires:
                   # open("flag.txt") - flag on stdout
```

The payload bytes:

```text
00 00 02 01                  data[0..1] = 0 (refcount), data[2] = 2 (sticker-from-file), data[3] = 1
08 00 00 00                  data[4] = 8 - the length of "flag.txt"
<ticket 8 bytes>             data[8..15] - the leaked ticket
"flag.txt\0" + padding       path at data+0x10
```

A note on the check: the second branch requires `data[2] = 2`, `data[3]` non-zero and `data[8..15] = ticket`. Ours has `data[3] = 1` - qualifies. Everything happens in one process, so the ticket is the same one we leaked in phase 1.

## Why both phases must be in one process

`booth_ticket` is generated once at process startup from `/dev/urandom`. A new connection/process has a new ticket. So the exploit is interactive: send phase 1, read the leaked ticket from the output, send phase 2 with that ticket - all in the same session. The scripts do exactly that.

## Verification, locally first

I created a test directory with the original `chall`, a synthetic `flag.txt` = `FortID{LOCAL_TEST_ONLY_ue4f}`, and ran the proof script (it feeds phase 1, parses the ticket, feeds phase 2):

```text
ticket: a222e7f2ce978a8b
flag found: FortID{LOCAL_TEST_ONLY_ue4f}
RESULT: FILE SUBSTITUTION OK
```

Then the same script with a wrong ticket produced "[broken sticker]" - confirming the ticket check really gates the second branch (negative control).

## Remote

The same two-phase exchange over TLS against the instance (`sticker-booth-<...>.inst18.ctf.fortid.com:1337`). The service banner is "Sticker Booth recipe runner"; after phase 2 the flag was printed: `FortID{I_h0p3_Y0u_W1ll_Pu7_Th15_5t1ck3r_0n_Y0ur_L4p70p_<hash>}` (masked here).

## What I tried first (dead ends)

1. **Direct path substitution with mono.** The first idea: use the off-by-one to overwrite a builtin's path directly. It cannot work: mono only zeroes 3 of every 4 bytes, so it can never write consecutive "flag.txt" bytes; and it zeroes `data[2]` (turning the builtin into "broken") instead of setting it to 2. A zero-only write cannot forge the needed fields.

2. **Upload as the file-read primitive.** Upload objects are type 3, and `print` for type 3 only prints "[custom sticker]" - it never reaches the type-2 file-open branch. An upload alone cannot read files.

3. **Ticket surviving a reuse.** First I hoped a freed builtin could be reused "as-is" and keep its ticket. But `xalloc` zeroes the pool tail on reuse, wiping the ticket. The ticket only survives inside an untouched builtin block - which is exactly why we leak it from a live one.

4. **LFI without a leak.** The second branch checks `data[8..15] = booth_ticket` (8 random bytes). Guessing is pointless; the leak (phase 1) is what makes the whole chain feasible. The "captions alias + mono zero-refcount + remove frees + upload reuse + adjacent builtin + controlled fwrite length" choreography is the minimal one that both leaks the ticket and later replays it.

## Final scripts

- `scripts/proof_sb.py` - local proof: interactive two-phase (phase 1 leaks the ticket, phase 2 substitutes the path), prints the flag found.
- `scripts/solve.py` - the same chain over TLS for the remote instance.

## One-paragraph version

Sticker Booth is a non-PIE binary with a 16 KiB bump pool. Two bugs combine: the `mono` filter draws H+1 rows (a zero-only off-by-one into the next pool block), and `caption` deduplicates identical texts, so two items share one buffer guarded by a refcount. We zero the refcount with mono, remove one caption to free the shared buffer while the other caption still points at it (UAF), then re-allocate that block with an upload whose fields we control and place a builtin block right after it. `print` then leaks 32 bytes past the reused block - including the 8-byte booth_ticket. With the ticket in hand, we free the block again and upload a forged builtin header (type 2, valid ticket, path "flag.txt"); the dangling caption now hits the second branch of `print`, and the binary dumps flag.txt. All in one process, because the ticket is per-process.

## Lessons learned

1. A zero-only adjacent overwrite (off-by-one) looks useless at first, but it shines when the target is a refcount: zeroing it turns a shared buffer into a freeable one while a stale pointer remains (UAF).
2. Deduplication is a security-relevant feature: "two objects share one buffer" plus a refcount is a rich primitive.
3. When a random value gates a special path (the ticket here), look for a leak of that value BEFORE planning the main exploit; the leak choreography is often the harder half.
4. First-fit reuse allocators with zero-on-reuse reset object fields predictably - abuse the zeroing as a feature (we need zeros in most header fields anyway).
5. Always prove the gate: a wrong ticket gives "[broken sticker]" (negative control), the right one gives the flag.
6. One-connection workflows matter: per-process randomness means the leak and the payload must share a session; write the script as a two-phase interactive exchange, not two independent runs.