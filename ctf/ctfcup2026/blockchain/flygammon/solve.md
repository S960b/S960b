# Flygammon: 15 wins against a predictable VRF

[По-русски](solve.ru.md)

## 0. The obstacle

The contract runs a backgammon-like game against a bot and requires fifteen consecutive player wins. Dice look random, the opponent moves with a learned evaluation model, and a single bad roll resets the streak. Playing honestly is not a plan.

The way in was the randomness itself:

1. Recover the hidden VRF exponent.
2. Predict both players' dice for any game id.
3. Simulate the opponent policy and submit only games we win.
4. Use the `skip()` bug to keep the streak while looking for the next such game.

## 1. The VRF exponent is recoverable

The public parameters satisfy `H = G^s mod P`, which is a discrete logarithm. It is only hard if the group order is hard to factor. Here `P - 1` was smooth, the largest prime factor around one million, so Pohlig-Hellman plus the Chinese remainder theorem gives `s` quickly.

For the first instance:

```text
s = 357653648880297186077052514432982755682505315306081217084424960751681404340
P = 88322446169367629588592423935113078568751125482404658974766148135586794479379
```

Verification is one line, `pow(G, s, P) == H`. The stronger check was byte-for-byte agreement between our `uFor(gameId, turn)` and the contract's own output on several sample pairs. Without that, recovering `s` proves nothing.

Why this step: once the dice are computable, the game stops being a game of chance and becomes an offline search problem.

Important trap: a new instance is a new deployment. The second instance handed out different `P` and `H`, so both the secret and the whole plan had to be recomputed. Reusing the first plan would have failed on the first roll.

## 2. Why the streak survives skipping

`skip()` increments `gameId` and resets the game, but it does not touch `streak`. So the player may discard any number of games between two wins. There is no penalty for searching for the next favorable id, only the cost of the transactions.

Why this step: this is the actual bug. Everything else is prediction work; without `skip()` preserving the streak, even perfect dice knowledge would need fifteen wins in a row on consecutive ids.

## 3. The opponent is deterministic, so simulate it

Knowing the dice is not enough: the bot's replies must be predicted too. The handout contains `model.fly` (92 820 bytes, that is 23 205 float32 weights, a 203-80-80-5 network) and the Rust source under `model-src` with the `choose_2ply` policy.

The plan generator reimplements legal move generation and the two-ply evaluation in Python, with an expectation over the 21 dice combinations and a shortlist of eight moves for the one-ply pass. Float32 rounding matters near ties, which is why the last step was not Python at all: the original Rust binary `flynative` was built and used as the reference policy.

Validation before touching the chain:

- 679 half-turns of the final plan pass an independent legality checker.
- Predicted opponent moves match the original Rust implementation for 332 of 332 compared decisions.

Why this step: a plan that is legal but disagrees with the bot's tie-breaking loses the game it was built for.

## 4. Executing on chain

The first instance (plan ids 26 to 305, 291 skips) was played up to a streak of 11 and then the instance expired mid-run. That run is history, not the result.

The successful run used a second instance with new parameters, a freshly recovered secret and a new plan:

```text
6, 11, 34, 79, 138, 177, 184, 220, 273, 296, 325, 358, 407, 432, 482
```

The replayer was sped up in two ways: pre-signed sequential nonces allow the 467 `skip()` transactions to be sent as batches, and game state is read with a single JSON-RPC batch instead of six sequential calls. With that, all fifteen wins went through in about 318 seconds, `streak` reached 15 and `isSolved()` returned true.

The Anvil debug method for writing storage directly (`anvil_setStorageAt`) was rejected by the competition RPC (`method not permitted`), so the win came from the prediction path only, with no state forgery.

Why this step: the streak is monotone only if each commit is preceded by a check that the on-chain dice and board still match the simulation. The replayer does exactly that and resumes from the current `streak` after a dropped connection.

## 5. Result and evidence

The server returned the flag once the contract reported solved. It is masked in this repository as `ctfcup{...}`.

What is reproducible from the surviving bundle: the VRF recovery (`scripts/verify_vrf.py`), the plan, the legality check, and the Rust parity check. What is not in the repository: the wallet key, the live RPC endpoint and the transaction hashes of the final run.

## 6. The flaw

The game assumed dice are unpredictable to the player before committing. A smooth-order group broke that: the secret exponent falls to Pohlig-Hellman, and then the whole game is deterministic. Combined with `skip()` preserving the streak, the fifteen-win condition collapses into "find fifteen winning ids and pay for the transactions".

## 7. Lessons

- Check the group order before assuming a discrete log is hard.
- Recover, then verify against the live contract; a wrong secret looks exactly like a right one until you compare outputs.
- Predict the opponent, not only the dice; ties are where naive ports lose.
- Never reuse a plan across deployments.

One paragraph: the contract's dice come from a VRF whose modulus group has a smooth order, so Pohlig-Hellman recovers the secret exponent; with dice predicted, a Python port of the Rust two-ply bot searches for winning game ids, `skip()` lets the player discard the rest without losing the streak, batched pre-signed transactions play the fifteen selected games in about five minutes, and the contract reports solved.
