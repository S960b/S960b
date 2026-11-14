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
