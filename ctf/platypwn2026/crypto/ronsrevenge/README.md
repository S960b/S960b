# Ron's Revenge

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** Platypwn 2026
- **Category:** crypto
- **Difficulty:** 2
- **Materials:** `server.py`; exploit process check/log path (actual exploit absent)
- **Flags:** 1 accepted, exact strings masked (`PP{...}`)

A chosen-IV stream cipher challenge using an RC4-like key schedule with a state of size 2048.

**Core issue:** The service seeds an eight-word long-term key from the flag and prefixes every key with a client-supplied three-word IV. The state is a permutation of 0..2047, and each RC4 output is 11 bits; the stream packs those bits into bytes before XORing the flag. Because the flag begins with the known `PP{` format, every encryption request leaks initial keystream bits. The biased KSA statistics survive the expanded state size.

See the [full solving notes](solve.md).


## Recovered original working solver

Recovered original FMS solver, including RC4-2048 KSA, weak-IV voting, 11-bit keystream extraction from a known two-character prefix, full-key verification and a local --selftest. The self-test's synthetic plaintext was replaced by an obvious non-flag marker for public release.

```bash
python3 scripts/ronsrevenge_exploit.py <host> <port>
```

Code: [scripts/ronsrevenge_exploit.py](scripts/ronsrevenge_exploit.py). The file contains the algorithm and checks, not a hardcoded live service address.
