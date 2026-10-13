# Baby Crypto - AES-CTR with a nonce taken from the plaintext

- **Platform:** FortiD CTF 2026
- **Category:** Crypto
- **Author:** (not recorded in the workspace)
- **Points / Solves:** (not recorded in the workspace)
- **Date:** 2026-10-13
- **Remote:** `ncat --ssl <host>:1337` (the platform gives an instance; dead after the event)
- **Flag:** `FortID{...}` (masked)
- **Artifact:** `chall.py` - the source, shipped with the task

## What the task is

The service is a "secure vault": at startup the flag is put into it, and the player has two operations - store their own secret and export the whole vault as JSON (`Secret #0` is the flag ciphertext). The whole session runs under `signal.alarm(15)`.

The entire bug is one line of `chall.py`:

```python
def encrypt(self, plaintext):
    cipher = AES.new(self.key, AES.MODE_CTR, nonce=plaintext[:8])
    return cipher.encrypt(plaintext)
```

The nonce is taken from the first 8 bytes of **the plaintext itself**, and the key is one per connection. So by submitting a message with the same first 8 bytes as the flag, we get the same keystream and recover the flag.

The first 7 bytes are known (`assert FLAG.startswith("FortID{")`), so only one byte is brute-forced - `FLAG[7]` - and the hit is determined unambiguously: `(C_flag XOR C_mine)[:8] = 0`.

The full analysis and exploit are in [`solve.md`](solve.md).

## Folder structure

```text
crypto/baby-crypto/
├── README.md            # this file: the task context
├── solve.md             # the solution: analysis, pitfalls, flag
├── chall.py             # the challenge source
└── scripts/
    └── solve.py         # the working exploit: python3 solve.py <host> [port]
```