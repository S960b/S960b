# Lost Archive (crypto)

The challenge ships two files: `generate.py` and `output.txt`. The name refers to the
original archival package that was lost and recovered from a web archive, the generator
and its output are what survived.

`generate.py` describes the scheme:

- `E = 65537`, four independent RSA instances, primes of 1024 bits, modulus of about 2048 bits.
- The 32-byte flag is split into four 8-byte chunks.
- Each instance encrypts one chunk, padded on the left with 24 random bytes, so the message
  is 32 bytes and the chunk occupies the least significant 8 bytes.
- For each instance the output also contains `h`, the 518 most significant bits of one
  prime, that is `h = p >> 506`.

`output.txt` starts with `4`, `65537`, `32` and then holds four triples `(n, c, h)`.

Goal: recover all four chunks, concatenate them in order and get the flag in the
`SOLARCTF{...}` format.

Files:

- `assets/generate.py`, `assets/output.txt` - the challenge as shipped
- `scripts/solve_rsa_partial.py` - solver
- `scripts/run_kali.sh` - wrapper that installs local dependencies and starts the solver
- `evidence/` - run summary and timing notes

A full solving path is in `solve.md`.
