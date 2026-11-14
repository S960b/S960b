# Unholy Trinity: modular RSA hash collisions and exact-root authentication

[По-русски](solve.ru.md)

## 1. Goal, evidence and reproduction

Two independent defects in the supplied RSA authentication service lead to two flags: distinct fixed-length passwords can collide under `h(x)=x^17 mod N`, and the server discloses the RSA commitment for a short 19-byte admin secret. The latter allows exact integer-root recovery because the exponentiation does not wrap modulo N.

**Result:** both flags obtained and accepted. The original recovered two-part solver is [`scripts/solve_unholytrinity.py`](scripts/solve_unholytrinity.py). Run `python3 scripts/solve_unholytrinity.py HOST PORT`; it reproduces both registrations, extracts the root and completes 52 authentication rounds with `r=1`.

**Evidence:** the original [`server.py`](assets/server.py) and solver. Full interactive per-round terminal output is unavailable; the mathematics and exact protocol inputs are visible in source.

## 2. Exact solver mathematics

There are two independent vulnerabilities in the actual `server.py`.

**Registration.** The public modulus `N=pq` is approximately 2674 bits and `e=17`. The service accepts passwords of exactly `NLEN=ceil(bit_length(N)/8)` bytes, checks raw-byte uniqueness and hashes a password as `h(x)=x^17 mod N`. For a suitable integer `x`:

```text
a = N + x
b = 2N + x
a != b
a mod N = b mod N = x
a^17 mod N = b^17 mod N
```

Thus distinct byte strings have equal RSA hashes. The recovered `scripts/solve_unholytrinity.py` initializes `x` with `b"Aa1+"` and increments it until both numbers fit into `NLEN` bytes and pass the server's policy. A subtlety: the policy searches for character classes in `str(password)`, a Python representation of the byte string, not in the raw bytes. The solver exactly reproduces that faulty check.

The registration code prints `FLAG0` when the hash collides with an earlier registration. It does not return afterward. The script uses `player0` and `player1` and does not try to register a forbidden `admin` account.

**Administrator recovery.** The admin secret is 19 bytes, at most 152 bits. Raising it to the 17th power produces at most 2584 bits, smaller than the 2674-bit modulus. Therefore `m^17 < N` and the published hash is the ordinary, unreduced integer power:

```text
admin_hash = m^17
m = exact_integer_17th_root(admin_hash)
```

The script computes the exact root using binary search and validates it by exponentiation. This is **not RSA factorization**.

**52 identification rounds.** The source computes `ROUNDS = 256 // e.bit_length() + 1 = 52` and asks for:

```text
response^17 == announcement * admin_hash^challenge  (mod N)
challenge ∈ {0,...,16}
```

The original successful solver uses `r=1`, sends `announcement=1`, and replies `response=m^challenge mod N`. A general randomized proof could use `r^17` and `r*m^challenge`, but the original code really did use 1. The documented script is therefore faithful to the execution path, not just algebraically plausible.

## 3. Reproduction and sanity checks

```bash
python3 scripts/solve_unholytrinity.py <host> <port>
```

The script prints the modulus size, both registration outcomes and `FLAG0`; it checks `pow(m,e,N)==admin_hash` before the admin rounds and extracts `FLAG1` only after the final response. A standalone modular-collision test can validate the first flaw without network access.

## 4. Why the design failed

1. `pow(password,17,N)` is not a password hash; it identifies residue classes modulo N.
2. Fixed-length input does not make the map injective.
3. Publishing `pw_hash` of a short secret with a small public exponent reveals the secret by exact root extraction.
4. More zero-knowledge-style protocol rounds cannot restore secrecy once the witness itself has been exposed.


## Local challenge environment

The original [`Dockerfile`](assets/Dockerfile) and [`compose.yaml`](assets/compose.yaml) now live alongside the service sources in [`assets/`](assets/). Compose defines **dummy flags**, not competition flag values. With Docker Compose installed:

```bash
cd ctf/platypwn2026/crypto/unholytrinity/assets
docker compose up --build
# From another terminal in the same directory:
python3 ../scripts/solve_unholytrinity.py 127.0.0.1 1337
```

This recreates the protocol with freshly initialized server state. New keys and random values will not match the historical live deployment. Docker build execution was not independently tested during this documentation audit.
