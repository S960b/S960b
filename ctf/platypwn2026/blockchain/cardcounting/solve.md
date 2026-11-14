# Card Counting: solving notes

[По-русски](solve.ru.md)

## 1. Challenge

A casino contract uses an off-chain Python backend to deal blackjack hands and settle roulette. The dealer randomness and on-chain settlement were the key surfaces.

**Materials:** `Chal.sol`, `Setup.sol`, `dealer.py`.

## 2. Analysis

In `dealer.py`, `rng = random.Random()` is a process-wide Mersenne Twister PRNG. `deal_hand()` draws only 32 bits (`getrandbits(32)`) and repeatedly applies `divmod(number,52)` to manufacture six cards; this is not equivalent to uniformly drawing six independent cards. With six base-52 digits the high cards are strongly biased (the sixth base-52 digit is restricted to 0..11 because 52^5 = 380,204,032 and a 32-bit number is below 2^32). `spin_wheel()` uses the *same* `rng` for results 0..36. The contract allows a roulette bet of 0.1–0.5 ETH and pays 36 times a correct guess; the victory condition requires reducing 100 ETH below 10 ETH.

## 3. Solving sequence

1. Inspect the off-chain dealer's `getrandbits(32)`/base-52 conversion and its shared PRNG for roulette.
2. Use observable `HandDealt`/`SpinSettled` events and the public `getHand` method to study produced values; a 32-bit sample embedded in six base-52 digits is much more informative than a genuine six-card random draw.
3. The agent report says the team used card prediction/counting to win enough funds from the casino. Exact state-recovery/prediction code was not preserved and cannot be recreated as an asserted successful exploit from the supplied evidence.
4. Validate the goal through `Setup.isSolved()` once the contract balance is under 10 ETH.

## 4. Recorded result

The backend and contract source confirm the RNG design, event observability, payout equations and balance threshold. The archive preserves only the final platform submission command for the accepted flag.

1 accepted flag(s); full strings masked `PP{...}`.

## 5. Limits

There is no solver, PRNG state-recovery trace, transaction history or proof of the exact sequence of wins. The randomness analysis is independently derivable; the reported winning sequence is not reproducible from the archive alone.

## 6. Mitigation

Do not generate economic outcomes with a predictable off-chain PRNG observable through on-chain events. Use a verifiable, manipulation-resistant randomness mechanism and avoid easy profit amplification from weak payouts.
