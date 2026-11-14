# Ron's Revenge: solving notes

[По-русски](solve.ru.md)

## 1. Task and source material

A chosen-IV stream cipher challenge using an RC4-like key schedule with a state of size 2048. Source files: `server.py`; exploit process check/log path (actual exploit absent).

## 2. Analysis

The service seeds an eight-word long-term key from the flag and prefixes every key with a client-supplied three-word IV. The state is a permutation of 0..2047, and each RC4 output is 11 bits; the stream packs those bits into bytes before XORing the flag. Because the flag begins with the known `PP{` format, every encryption request leaks initial keystream bits. The biased KSA statistics survive the expanded state size.

## 3. Solving procedure

1. Send a controlled sequence of three 11-bit IV words and record the ciphertext for the same unknown flag.
2. XOR its known prefix (`PP{`) with each ciphertext prefix to recover corresponding keystream bits.
3. Apply FMS-style weak-IV reasoning to the first RC4 output; generate votes for successive eight unknown key words, recomputing known-prefix KSA steps for each stage.
4. Rank candidates by frequency, test the assembled key against additional IV/ciphertext pairs, then regenerate the stream and decrypt the remaining flag bytes.

## 4. Evidence and result

The source shows the 3+8 key layout, arbitrary chosen IVs, `state_size=2048`, and byte packing. The challenge was solved with `ronsrevenge_exploit.py`; the attached evidence shows its process/log check but not the exploit itself. The flag was accepted.

Result: **1 accepted flag(s)**, masked `PP{...}`.

## 5. Reproduction limits

Without the original vote tables or exploit code, exact IV families, sample counts and final key words cannot be independently stated. The method is corroborated by the previous solver report, not the command snippet alone.

## 6. Fix and lesson

RC4 and RC4-like KSA variants should not be used with attacker-controlled IV prefixes. Use a nonce-based AEAD with established security properties.


## Recovered original working solver

Recovered original FMS solver, including RC4-2048 KSA, weak-IV voting, 11-bit keystream extraction from a known two-character prefix, full-key verification and a local --selftest. The self-test's synthetic plaintext was replaced by an obvious non-flag marker for public release.

```bash
python3 scripts/ronsrevenge_exploit.py <host> <port>
```

Code: [scripts/ronsrevenge_exploit.py](scripts/ronsrevenge_exploit.py). The file contains the algorithm and checks, not a hardcoded live service address.


## 7. The actual recovered solver

The supplied `server.py` sets the permutation size to `N=2048=2^11` and constructs the RC4 key as:

```text
K = [IV_0, IV_1, IV_2, k_0, k_1, ..., k_7]
```

The client chooses the three IV words. The eight secret words are generated once using `random.seed(flag)` and reused for unlimited encryption queries.

**Recover the first 11-bit keystream word.** The PRGA emits words of 11 bits, while `Stream.get_prg_byte()` packs these into bytes. XOR the first two ciphertext bytes with the known prefix `PP`, and then discard the final five bits:

```python
bits16 = ((ct[0] ^ ord('P')) << 8) | (ct[1] ^ ord('P'))
first_word = bits16 >> 5
```

Using only one ciphertext byte would lose three necessary bits and defeat the key recovery.

**Weak-IV voting.** For each unknown key index `B=3..10` the script sends:

```python
IV = (B, N - 1, x)
# x runs from 0 to samples-1
```

The default `samples=384` gives up to 3,072 oracle queries over eight secret words, not an astronomical `2048^8` brute-force search.

The selected IV biases early KSA swaps. Given earlier recovered key words, the solver computes the state `S_B` after the first B rounds and votes for:

```text
K[B] = inverse_position_of(z in S_B) - j_B - S_B[B] (mod 2048)
```

Here `z` is the first observed 11-bit PRGA word. Every sample increments a candidate counter. The top candidate, runner-up and vote margins are printed; low vote counts or weak margins trigger a confidence warning.

**Cross-IV validation.** The solver saves ciphertexts under eight separate IVs. After recovering the key, it runs its own RC4-2048 KSA/PRGA implementation and the exact 11-bit-to-byte packer to decrypt all eight. A correct key must produce identical plaintext under all eight and match the known prefix. This check guards against selecting an unlucky high-count FMS candidate.

## 8. Running the script

```bash
python3 scripts/ronsrevenge_exploit.py --selftest
python3 scripts/ronsrevenge_exploit.py <host> <port> --prefix PP --samples 384
# If the vote margin is weak:
python3 scripts/ronsrevenge_exploit.py <host> <port> --prefix PP --samples 512
```

The self-test uses a synthetic local plaintext and Python's seeded random generator, then asserts the full recovered key matches the local key. The public synthetic fixture is deliberately not a real flag. This is the exact recovered solver, not an invented substitute for a missing one.

## 9. Why a larger state did not remove the attack

Expanding RC4 from 256 to 2048 permutation elements changes the output word size but leaves the weak `IV || secret` key-scheduling pattern. Chosen-IV sampling still reveals biased early-state information. A larger state is not a substitute for nonce/key separation or a modern AEAD.
