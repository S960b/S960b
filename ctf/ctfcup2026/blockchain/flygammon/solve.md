# Flygammon: solving a 15-win blockchain game with a recovered VRF key

[По-русски](solve.ru.md)

## 0. The goal and the obstacle

The challenge was an on-chain, backgammon-like game against a bot. Its solved condition required **15 consecutive player wins**. Every game used dice generated through a verifiable-random-function-like construction, and the bot selected moves using a learned evaluation model.

At first glance this sounds like a strategy problem. But a single unlucky roll can reset a long winning streak. The better question was whether the supposedly random dice could be known before choosing which games to play.

The approach had three layers:

1. Recover the hidden VRF exponent.
2. Predict both players' dice for candidate game IDs.
3. Simulate the full opponent policy and submit only favorable games.

## 1. Why the VRF was predictable

The public data included a finite-field modulus `P`, group element `G` and public value `H` satisfying

```text
H = G^s mod P
```

where `s` is the secret exponent. Recovering `s` appears to be a discrete logarithm problem. Usually that is difficult in a properly selected large prime-order group.

Here the relevant group order was **smooth**. Its largest factor was around one million. This made Pohlig-Hellman practical. The algorithm solves discrete logs modulo each prime-power factor of the group order and combines the residues with the Chinese remainder theorem.

The recovered value in the research archive was:

```text
s = 357653648880297186077052514432982755682505315306081217084424960751681404340
```

The essential verification was:

```python
assert pow(G, s, P) == H
```

The live work also reported byte-for-byte agreement of locally predicted `uFor()` outputs with the contract's outputs. Recovering `s` would not be useful otherwise.

## 2. Why knowing dice changes the game

After the VRF secret was known, future dice were deterministic. The solver could inspect a game ID without spending a win on it.

The contract exposed `skip()`, so the player could move past unfavorable IDs until a good one appeared. The strategy did not involve forging a victory or bypassing the 15-win condition. It selected games for which the projected player had a winning line against the actual bot.

The offline plan identified these winning game IDs:

```text
26, 46, 67, 82, 85, 103, 117, 152,
168, 204, 214, 253, 274, 297, 305
```

Its accounting required **291 skip operations** and **15 commit operations**. The game token budget supported the commits. The game IDs came from local simulation and should not be treated as a universal plan for a different contract deployment without fresh validation.

## 3. The opponent model was not random

The handout included `model.fly`, a neural model file and Rust source under `model-src`. The binary model measured **92,820 bytes**, or **23,205 float32 weights**, corresponding to an **203 to 80 to 80 to 5** network.

This mattered because knowing the dice alone is not enough to guarantee a win. We must also predict which legal move the opponent will choose.

The Rust agent used `choose_2ply`. The research reimplemented legal move generation and the two-ply evaluation in Python, including an expectation over 21 dice combinations and a shortlist of eight moves for one-ply evaluation. Getting the move ordering, board encoding and float32 arithmetic right was critical.

The solver was verified two ways:

- A full plan with **15 simulated wins** and **757 half-turns** passed independent legal-move checking.
- The predicted opponent choices matched the original Rust implementation for **371 out of 371** compared decisions.

When exact actions have equivalent resulting board states, the board is the meaningful check for future prediction. Small float rounding differences near ties still matter, which is why native Rust comparison was important.

## 4. Replay with verification after every transaction

A live replayer must not blindly broadcast the whole plan. It should verify the expected contract and state, skip to the intended game ID, commit, wait for the oracle to post dice, and submit player moves only if the observed dice and board match the simulation.

The original bundle was called `flygammon_15wins_solver.zip`. It contained:

```text
flyengine.py      rules and neural model
flysolve.py       VRF rolls, planning and simulation
plan_15.json      predicted 15-win sequence
verify_plan.py    legality checks
flynative.rs      original Rust policy comparison
compare_native.py parity checker
play_live.py      guarded RPC transaction replayer
```

The recorded offline validation commands were:

```bash
python3 -m pip install numpy pycryptodome
python3 flysolve.py test --model ../handout/model.fly
python3 verify_plan.py plan_15.json
./build_native.sh ../handout/model-src
python3 compare_native.py --plan plan_15.json --native ../handout/model-src/target/release/examples/flynative --model ../handout/model.fly
```

For live use, the recorded `play_live.py` accepted a dry run without `--execute`; broadcasting required `--execute`, a matching contract, Rust verification, a fresh instance and the player's own private key stored outside the repository.

No wallet secret or live RPC endpoint is published.

## 5. What actually happened online

The offline plan was complete and independently checked. On live instances, the recorded progress reached a streak of **9**, later **11** consecutive wins, before an instance timeout. We did not preserve the terminal output showing the final jump from 11 to 15.

Separately, the team submission list records **Rovny robot** as accepted on October 31, 2026, for **642 points**. That supports the event-level solved status, but does not supply the missing chain transaction hashes or exact final sequence.

It would be misleading to write that the archived local simulation itself proves the final on-chain win. The two forms of evidence answer different questions.

## 6. The central flaw

The challenge's security depended on dice not being predictable to the player before the game. A smooth-order discrete log broke that assumption. Once the state of the supposed random process was known, the game became an offline search problem against a deterministic opponent.

The most important part of the exploit was not a clever move during a single game. It was choosing which games to play after predicting the entire sequence.
