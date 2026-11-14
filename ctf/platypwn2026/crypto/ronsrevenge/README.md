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
