# VoteMe: solving path

## 0. The whole plan

The handout is a bundle: a Ruby voting service source (`record.rb`, `gateway.rb`,
TLS-with-custom-ECDH client/server, PLONK proving circuit and zkey), and a PCAP of
a "campaign" run - every secret vote the server logs, plus 1000 published
ballots. The goal is to recover the secret votes, which encode the flag in base 3
(420 votes = 70 bytes).

The chain closes six distinct weaknesses, each one independently small:

1. The custom curve's order is divisible by 2^16, so a 2-adic subgroup attack
   recovers the low 16 bits of every ECDH private scalar from the public point.
2. All private scalars come from one Ruby `Random.new` (MT19937). The low bits
   form a linear system over GF(2); solving it determines the last TLS scalar.
3. With that scalar and the ClientKeyExchange, the premaster is derived, the TLS
   AES-256-GCM session decrypts (GCM tag verifies), and the `/votes` JSON with
   1000 ballots is plaintext.
4. Each ballot reveals V8 `Math.random()` outputs through the `@lukeed/uuid` v2
   bug; linear algebra recovers the xorshift128+ state (64 candidates), next
   outputs are predictable.
5. The PLONK blinding scalars are also `Math.random()`-derived; recomputing the
   commitment `A` for votes 0,1,2 picks the right vote for every ballot.
6. Decode the first 420 votes in base 3 (6 trits per byte) to ASCII.

## 1. The layout of the challenge

Why this step: understand what data exists and where, before touching the crypto.

- `record.rb` runs the voting campaign and writes `traffic.pcap`: 420 secret
  votes (server-side records), then `populate(1000-used)` each "vote" being a
  separate TLS connection (1741 connecting SYNs total in the capture), then one
  `GET /votes`.
- The custom TLS uses a prime-order-subgroup curve defined in `curve.rb`
  (`Curve::ORDER`, `Curve::G`); server ECDH scalars are generated with
  `Random.new(...).bytes(32)`.
- Each ballot is a PLONK proof created in the browser: a circom circuit, the
  `vote_plonk.zkey` proving key, and `@lukeed/uuid` 2.0.1 for beta IDs.

The PCAP plus the service source is everything: no challenge server survives, so
the solve is fully offline.

## 2. Low bits of the ECDH scalars

Why this step: `ORDER` being even lets us divide repeatedly; the small 2-power
subgroup makes a discrete log trivial.

`Curve::ORDER` is divisible by 2^16, and `G` has a nonzero component in the
2^16-torsion subgroup. For each server public point in every TLS
ServerKeyExchange:

```text
P = k*G;  k mod 2^16 = dlog over the 2^16-subgroup of P
```

The 2-adic ladder costs about 2^16 subgroup operations but in practice is far
cheaper with the right basis; the saved observations (`observed_lowbits.txt`) are
1741 values of `scalar % 65536`.

## 3. Breaking the shared MT19937

Why this step: the server uses one RNG for all scalars, so "random" is a
deterministic bit sequence.

`Random#bytes(32)` consumes 8 consecutive 32-bit MT19937 outputs; the low 16 bits
of the scalar equal the low 16 bits of the first word (the order's 2^16 factor
preserves them). 1741 such observations give 27856 linear equations over the
19968-bit MT19937 state; Gaussian elimination over GF(2) leaves 15 free bits in
the unused part of one word and predicts the last scalar exactly. The prediction
is checked against the last public point (it matches).

## 4. TLS decryption

Why this step: with the last ECDH scalar and the client's key exchange message,
the session keys follow from the standard TLS PRF.

Compute premaster = `client_pub ^ server_priv` (on the custom curve), run the
TLS PRF (SHA-384), derive AES-256-GCM keys/IV, and decrypt each record. The GCM
tag authenticates; the plaintext is the `/votes` JSON: 1000 ballots, each
`{proof, pubSignals, ...}`.

## 5. Recovering the V8 RNG state

Why this step: ballot IDs are made with a broken UUID library, leaking randoms.

`@lukeed/uuid` 2.0.1 uses `Math.random()` and publishes ~122 bits per UUID out
of the 128-bit xorshift128+ state - roughly 6 unknown bits per state, plus the
cache of 64 pre-generated outputs the V8 RNG keeps. The ballot's beta IDs give 16
consecutive `Math.random()` outputs. Linear algebra on those recovers the state
up to 6 free bits; enumerating 64 candidates gives the next output. The script
`v8_uuid.py` implements the state recovery.

## 6. Choosing a vote with PLONK

Why this step: the vote value is hidden inside the proof; the RNG makes it
checkable without a validator.

snarkjs in the browser falls back to `ffjavascript`, which seeds its ChaCha20
with `Math.random()` instead of a CSPRNG. From the recovered V8 state the
32 ChaCha20 seed bytes follow, hence the first two PLONK blinding scalars used
for commitment `A`. For each of the 64 state candidates and each vote in
{0,1,2}, recompute `A` (using the provided `vote_plonk.zkey`, the Powers of Tau
section and a Lagrange IFFT). Exactly one of 64x3 combinations matches every
ballot's original commitment - that combination identifies the ballot's vote.

## 7. Reading the flag

Why this step: the secret votes are the plaintext; group 6 trits per byte.

The first 420 recovered values are trits in {0,1,2}; group them 6 at a time:
`byte = sum(trit[j] * 3^(5-j))`. The resulting 70 bytes are the ASCII flag.
Votes 420..425 reproduce the known test cycle 1,0,2,1,0,2, which is a sanity
check that the whole chain is right.

`quick_verify.py` checks all of this offline: SHA-256 of the handout, 1000
votes, 426 unique rows, the six test votes and the exact 70-byte flag.

## 8. Reproduction

Why this step: the deliverable must run from the original PCAP, not from saved
conclusions.

```bash
python3 quick_verify.py          # offline check of saved evidence -> flag
bash run_reproduce.sh            # full chain from the original tar.gz (needs python3, cryptography, ruby, g++, libgmp-dev)
```

The full reproduction is heavy (GF(2) system `27856 x 19968`, plus recomputing
PLONK commitments), but is offline and needs no challenge server.

## 9. Dead ends (do not repeat)

- **Attacking the full discrete log of the curve.** Not needed: only the low 16
  bits are exposed and that is enough to fix the RNG equations.
- **Brute forcing MT19937 outputs.** 19968 bits is far beyond brute force; the
  linear system is the correct tool.
- **Guessing UUID seeds.** The cache of 64 pre-generated outputs made naive seed
  search fail; the state-recovery linear algebra handles it.
- **Trusting `votes.json` before decrypting.** The file is only trusted after
  the GCM tag verifies within the TLS records; anything else is not authentic.

## One-paragraph version

The custom curve is 2-adically weak and leaks low scalar bits; the server's RNG
is one MT19937 seeded once, so 1741 low-bit samples solve for the final TLS
scalar; the AES-GCM traffic decrypts; ballot IDs leak V8 `Math.random()` and
recover xorshift128+; the PLONK blinding randomness is the same RNG, so each
ballot's vote is found by matching recomputed commitments; 420 votes in base 3
decode to the flag.

## Lessons learned

- Composite subgroup orders deserve a 2-adic check before use; leaking 16 bits
  per key turns a "random" key generator into a fully predictive one.
- Reuse of a single RNG seed across protocols (TLS keys and proof randomness)
  collapses them into one equation system.
- When a proof system's randomness is reproducible, commitments become
  verifiers: matching `A` over the candidate vote set decides the hidden input.