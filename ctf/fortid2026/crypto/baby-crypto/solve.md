# Baby Crypto - writeup (AES-CTR with nonce = plaintext[:8], keystream reuse)

## Getting to know the task

The challenge ships the source (`chall.py`) and a remote service. The service is a "secure vault": at startup it stores the flag, and the player gets two menu options:

1. `Safely Store a Secret Value` - store your own secret in the vault;
2. `Export Vault Data` - dump the whole vault as JSON.

Task: recover the flag using only these two operations and the source.

The first thing I always do with a crypto challenge that ships source: read the source end to end and look for where the randomness comes from. The flag is protected by AES, so the key and the nonce are the interesting parts.

## How I read the code (what to look for)

- `chall.py` is the whole service, no reverse engineering needed.
- I looked for: where the key is generated, where the nonce is generated, whether the nonce is reused, and whether the plaintext influences the nonce.
- The interesting function is the `Cipher` class:

```python
class Cipher:
    def __init__(self):
        self.key = get_random_bytes(16)

    def encrypt(self, plaintext):
        cipher = AES.new(self.key, AES.MODE_CTR, nonce=plaintext[:8])
        return cipher.encrypt(plaintext)
```

Three facts stand out, and all three matter:

1. **The nonce is `plaintext[:8]`** - the first 8 bytes of the plaintext itself, not CSPRNG (cryptographically secure pseudo-random number generator, i.e. `get_random_bytes` here) output. So the nonce is chosen by the caller (us, when we store a secret).
2. **The key is generated once per connection** - `Cipher()` is created inside `Vault.__init__` as `self.cipher = Cipher()`, and `Cipher.__init__` draws 16 random bytes with `get_random_bytes(16)`; that key never changes between inserts.
3. **At startup the flag is already inserted**: `vault.insert(FLAG)`, so `Secret #0` in the export is the flag ciphertext.

## The math of the attack (why CTR makes this exploitable)

AES-CTR is a stream mode: it generates a keystream `KS = AES(key, nonce || counter)` and computes `C = P XOR KS`. The keystream depends only on `(key, nonce)` - **not** on the plaintext. (This is the standard definition of a stream cipher / CTR mode; see e.g. NIST SP 800-38A, Recommendation for Block Cipher Modes of Operation: the keystream is generated from the key and the counter block, independently of the plaintext.)

If two messages are encrypted with the same key AND the same nonce, the keystream is the same, and XORing the two ciphertexts cancels the keystream:

```text
C_flag XOR C_mine = (FLAG XOR KS) XOR (MINE XOR KS) = FLAG XOR MINE
```

This is the well-known "two-time pad" property: because `KS` appears twice, it cancels out. The XOR of the two messages is then equal to the XOR of the two plaintexts.

So if we can make the server encrypt our message with the same nonce as the flag, we learn `FLAG XOR MINE`. If we also know `MINE`, we recover `FLAG = C_flag XOR C_mine XOR MINE`.

## Knowing the first 8 bytes of the flag

The nonce is `plaintext[:8]`, so "same nonce" means our plaintext must start with the same 8 bytes as the flag:

```python
assert FLAG.startswith("FortID{") and FLAG.endswith("}")
```

The assert in `main()` tells us the first 7 bytes exactly: `FortID{`. So only **one** byte is unknown - `FLAG[7]`. For each candidate byte `g` in printable ASCII we build:

```text
MINE = "FortID{" + chr(g) + "B" * (len(FLAG) - 8)
```

The filler (`"B"` repeated) just makes the length match, so the ciphertext has the same length as the flag's. The length of the flag is known from the length of `Secret #0` (CTR does not pad).

## How to tell a hit without guessing

The nice part: the hit criterion is exact, no "looks like a flag" heuristic needed.

- If `MINE[:8] = FLAG[:8]`, then the keystreams match, so `C_flag XOR C_mine = FLAG XOR MINE` - and since the first 8 bytes of `FLAG` and `MINE` are identical, the first 8 bytes of this XOR are **zero**.
- If `g` does not equal `FLAG[7]`, the nonces differ, the keystreams differ, and the first 8 bytes of the XOR are (almost certainly) non-zero.

```text
criterion: (C_flag XOR C_mine)[:8] = b"\x00" * 8   means   g = FLAG[7]
```

After the correct `g` is found, the full flag is:

```text
FLAG = C_flag XOR C_mine XOR MINE
```

## Pitfalls (things that break a naive script)

1. **`signal.alarm(15)`** in `main()` - the whole session has 15 seconds. Asking the server one candidate at a time (a couple of round trips per candidate, ~95 candidates) does not fit. So the exploit **pipelines**: send all `1\n<secret>\n` commands in one packet, then `2\n3\n`, and parse the final export once.

2. **`input().strip()`** strips the secret on both ends. The candidates start with `F`, and the internal characters (including a possible space when `g = 0x20`) are not at the edges, so nothing is lost. Commands still have to be sent line by line.

3. **TLS** (`ncat --ssl`) - the script wraps the socket with `ssl` and disables certificate verification.

4. **The export JSON is glued to the prompt.** The menu prompts are printed **without** a trailing newline, so the JSON is glued to the `> ` prompt in one line: `...Enter secret: > {"Secret #0": "..."}`. A line-based parser silently breaks; the exploit parses the last `{"Secret #0":...}` match with a regex.

5. **The server closes the connection after `3`** (exit), so "the last JSON in the output" is the final vault state.

## The exploit

`scripts/solve.py` does exactly this chain:

1. Connect over TLS, read the banner.
2. Send `2` (export), parse `Secret #0` - the flag ciphertext, get its length `n`.
3. Build 95 candidates `"FortID{" + chr(g) + "B"*(n-8)` for `g` in `32..126` (printable ASCII).
4. Pipeline all inserts + export + exit in one send.
5. Parse the last vault JSON.
6. For each candidate: XOR with the flag ciphertext; if the first 8 bytes are zero - the nonce matched; recover the flag and print it.

The script is fully parameterized (`host`, `port`, `--timeout`) and exits 0 on success. The draft with a hardcoded instance host is intentionally not part of the repo.

## Verification

The instance is provided by the platform during the event, so a fresh host is needed:

```bash
python3 scripts/solve.py baby-crypto-xxxxxxxxxxxx.inst16.ctf.fortid.com 1337
```

Expected output:

```text
[*] flag ciphertext: 70 bytes
[*] pipelining 95 inserts + 1 export (server alarm is 15s)
[*] vault has 95 entries
[+] nonce match at guess 'S' (0x53): FortID{St0p_B31ng_Cr3at1v3_With_N0nc3s_...}

FLAG: FortID{St0p_B31ng_Cr3at1v3_With_N0nc3s_...} (masked)
```

(The first export alone also gives the flag length; the pipeline is what stays within the 15-second alarm.)

## Dead ends (what I considered and rejected)

1. **Brute-forcing the whole flag.** Not needed: 7 of the first 8 nonce bytes are known, so only 1 byte is brute-forced.
2. **CBC-style bit-flipping or padding oracle.** The service has no decrypt path and no padding; CTR has no padding at all. Nothing to oracle on.
3. **Recovering the key.** The nonce/keystream reuse gives us plaintext XOR, not the key. We never need the key - we only need one reused keystream, and the plaintext structure (`FortID{...}`) gives it to us directly.

## One-paragraph version

The vault encrypts every inserted secret with AES-CTR where the nonce is the first 8 bytes of the plaintext and the key is fixed per connection. The flag is inserted at startup, and its first 7 bytes are known (`FortID{`). By inserting 95 candidates that differ only in byte 7, exactly one of them reuses the flag's nonce/keystream; the exact hit is detected by `(C_flag XOR C_mine)[:8] = 0`, and the flag is `C_flag XOR C_mine XOR MINE`. The 15-second alarm forces one pipelined request instead of a per-candidate round trip.

## Lessons learned

1. In stream ciphers the keystream depends only on (key, nonce), never on the plaintext - so a nonce influenced by the plaintext is a direct keystream-reuse primitive.
2. When a flag prefix is enforced by an assert, it is not flavor text - it tells you exactly how many bytes of the nonce you already know.
3. A known-plaintext prefix plus one unknown byte turns a "recover the key" problem into a "recover one byte" problem; the zero-XOR check makes the hit detection exact.
4. Timeouts on the service are part of the challenge: pipeline the requests instead of doing one round trip per candidate.
5. Never put user-controlled plaintext into a nonce. A nonce must come from a CSPRNG and must never repeat under the same key.

## Files

- `chall.py` - the challenge source (as shipped)
- `scripts/solve.py` - the working exploit: `python3 solve.py <host> [port]`