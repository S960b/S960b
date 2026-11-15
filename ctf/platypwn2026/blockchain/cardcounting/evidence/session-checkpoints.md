# Card Counting: source and session checkpoints

Evidence cross-checked against the original `assets/dealer.py` and `assets/Chal.sol`:

- The Python backend uses one process-wide `random.Random` object for both games.
- Every `deal_hand()` consumes exactly one `getrandbits(32)` and reveals the resulting 32-bit word as six base-52 card indices.
- The session report from November 13 states that **624 hands were dealt**, the MT19937 state was recovered and the next roulette result was predicted. The report records successful subsequent roulette predictions and platform completion.
- The blockchain code requires `address(TARGET).balance < 10 ether`, grants `36 × bet` winnings on a correct number and permits `cashOut()` to withdraw them.
- An independent local program `scripts/recover_mt.py --selftest` reconstructs 624 words and validates the next 100 roulette outcomes against Python's actual RNG.

Accepted competition flag:

```text
PP{c0unt1ng_tw1st3d…Sga-f}
```

The independent script is a reconstruction of the algorithm, not a claim to be an exported original transaction driver.
