# Ron's Revenge: chosen-IV recovery of an RC4-2048 secret key

[По-русски](solve.ru.md)

## 1. Goal, evidence and reproduction

The service encrypts the same hidden flag under an RC4-like cipher with a **2,048-entry permutation**, using a client-controlled three-word IV followed by an eight-word secret key. This `IV || secret` key-scheduling construction preserves the weak-IV statistical bias even though the state is larger than RC4's usual 256 bytes.

**Result:** the flag was recovered and accepted. The original recovered FMS solver is [`scripts/ronsrevenge_exploit.py`](scripts/ronsrevenge_exploit.py), with a local `--selftest`, chosen-IV sampling, eight-word key recovery and cross-IV validation. Run `python3 scripts/ronsrevenge_exploit.py --selftest` before connecting to a new challenge instance.

**Evidence:** the supplied [`server.py`](assets/server.py) and recovered solver. The exact instance key, vote tables and full live transcript were not archived; these are not fabricated below.

## 2. The actual recovered solver

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

## 3. Running the script

```bash
python3 scripts/ronsrevenge_exploit.py --selftest
python3 scripts/ronsrevenge_exploit.py <host> <port> --prefix PP --samples 384
# If the vote margin is weak:
python3 scripts/ronsrevenge_exploit.py <host> <port> --prefix PP --samples 512
```

The self-test uses a synthetic local plaintext and Python's seeded random generator, then asserts the full recovered key matches the local key. The public synthetic fixture is deliberately not a real flag. This is the exact recovered solver, not an invented substitute for a missing one.

## 4. Why a larger state did not remove the attack

Expanding RC4 from 256 to 2048 permutation elements changes the output word size but leaves the weak `IV || secret` key-scheduling pattern. Chosen-IV sampling still reveals biased early-state information. A larger state is not a substitute for nonce/key separation or a modern AEAD.
