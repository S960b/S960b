# Card Counting

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Category:** blockchain
- **Difficulty:** 2
- **Materials:** `Chal.sol`, `Setup.sol`, `dealer.py`
- **Flags:** 1 accepted (`PP{...}`)

A casino contract uses an off-chain Python backend to deal blackjack hands and settle roulette. The dealer randomness and on-chain settlement were the key surfaces.

**Root cause:** In `dealer.py`, `rng = random.Random()` is a process-wide Mersenne Twister PRNG. `deal_hand()` draws only 32 bits (`getrandbits(32)`) and repeatedly applies `divmod(number,52)` to manufacture six cards; this is not equivalent to uniformly drawing six independent cards. With six base-52 digits the high cards are strongly biased (the sixth base-52 digit is restricted to 0..11 because 52^5 = 380,204,032 and a 32-bit number is below 2^32). `spin_wheel()` uses the *same* `rng` for results 0..36. The contract allows a roulette bet of 0.1–0.5 ETH and pays 36 times a correct guess; the victory condition requires reducing 100 ETH below 10 ETH.

[Full solving notes](solve.md).
