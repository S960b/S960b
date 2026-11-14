# Card Counting: six cards hidden in one 32-bit random number

[По-русски](solve.ru.md)

## 0. Scope and result

The PlatyJack casino begins with 100 ETH and the Setup is solved at a remaining balance **below 10 ETH**. The deployed contract provides blackjack and roulette. Both games are settled by a Python service with a single process-wide `random.Random` generator.

The saved competition notes report that the team obtained and submitted the flag. The provided archive does **not** contain the actual winning solver, the PRNG state recovery trace, or the roulette bet history. This writeup therefore gives a reproducible **source-derived analysis** of the dealer, the exact mathematics of the weak card encoding, the on-chain settlement rules and clear boundaries on what the winning session's details can prove. It does not invent a successful script or unexplained 'MT19937 recovered' claim.

## 1. The dealer's actual source code

In `service/dealer.py`:

```python
rng = random.Random()

def deal_hand():
    number = rng.getrandbits(32)
    cards = []
    for _ in range(6):
        number, card = divmod(number, 52)
        cards.append(card)
    return cards

def spin_wheel():
    return rng.randrange(37)
```

The service draws **one 32-bit integer** for the entire six-card hand, not six independent integers in `[0,51]`. It decomposes that integer into base-52 digits, least-significant digit first:

```text
X = c0 + 52*c1 + 52²*c2 + 52³*c3 + 52⁴*c4 + 52⁵*c5
0 <= ci <= 51
0 <= X < 2³²
```

All six cards are emitted in the `HandDealt` event and can be queried through `getHand(id)`. Thus a single completed hand leaks its *entire 32-bit PRNG output value*, not six statistically independent card draws.

## 2. Exact high-card bias and leakage

The important powers are:

```text
52^4 =    7,311,616
52^5 =  380,204,032
52^6 = 19,770,609,664
2^32 =  4,294,967,296
```

Therefore:

```text
c5 = floor(X / 52^5) in {0, ..., 11}
```

It is **incorrect** to say the sixth card is always zero. It is not. It is restricted to the first **12** card IDs, with a skewed probability distribution. The likelihood `P(c5=0)` is `52^5/2^32 ≈ 8.85%`, while `P(c5=11)` is about 2.62%; uniformly random cards would give every ID 1/52 ≈ 1.92%.

The six independent card IDs would carry `6*log2(52) ≈ 34.20` bits. This generation method carries only 32 bits and contains a rigid arithmetic dependency. We can reconstruct the original word from an observed hand:

```python
def recover_word(cards):
    assert len(cards) == 6 and all(0 <= x < 52 for x in cards)
    x = sum(card * 52 ** i for i, card in enumerate(cards))
    assert x < 2**32
    return x
```

This inversion is **exact** because the base-52 decomposition is reversible. No brute force or guessing is needed for the word. Recovering the **whole Mersenne Twister state** requires enough outputs and proper handling of the fact that roulette calls consume the same stream. The ZIP does not preserve evidence of how that was accomplished during the accepted run.

## 3. Blackjack: payout and cost

From `Chal.sol`:

```solidity
uint256 public constant HAND_BET = 0.005 ether;
uint256 public constant DEALER_TIP = 0.006 ether;
uint256 public constant MAX_HANDS = 16;
```

Each `play(count)` must pay `count*(0.005+0.006) ETH`. A win pays `2*HAND_BET=0.010 ETH`, a push pays `0.005 ETH`, and a loss pays zero. Even if one won **every hand**, the payout of 0.010 ETH is less than the entry cost 0.011 ETH.

So blackjack alone is an **inherently negative expected net balance** for the player even under perfect outcomes. Its practical importance here is as an **observable random-word oracle** rather than a profitable method to drain 90 ETH.

The original order of six cards is specified:

```text
player: c0, c2, c4
dealer: c1, c3, c5
```

`score` stops drawing after the second card if the score has reached 17 and handles ace soft/hard conversion. This is important for reproducing payouts, but not necessary to invert the six cards back to the 32-bit word.

## 4. Roulette: why prediction matters financially

Roulette bets are `0.1 to 0.5 ETH`, the selected number must be 0–36, and the backend supplies `rng.randrange(37)` from the **same RNG** that produced the cards. A correct guess pays `36*bet` to the player's withdrawable `winnings` balance.

With a 0.5 ETH bet:

```text
win payout = 36*0.5 = 18 ETH
net reduction in shop balance from this spin (ignoring initial bet inflow) =
  +0.5 - 18 = -17.5 ETH
```

A chain of several correctly predicted spins can reduce a 100 ETH treasury below 10 ETH; a random number guess has probability 1/37 and is not reliably profitable. The source-derived route is to use revealed card words as samples of `rng`, determine its future `randrange(37)` outputs, then submit matching `spin(number)` calls before the backend settles them.

The precise successful algorithm is **not demonstrated by the archived final solve command**, so it is presented as the intended connection suggested by the shared RNG, not claimed as recovered historical MT state reconstruction.

### Important: `cashOut()` caps the payout at the treasury balance

The actual contract uses `amount = min(winnings[msg.sender], address(this).balance)` before transferring. Thus the last winning roulette claim **does not revert merely because the accrued winnings exceed the remaining treasury**: it pays the available balance and can bring the treasury to exactly zero. At a 0.5 ETH wager, five successful spins net `5 × 17.5 = 87.5 ETH` of treasury reduction (apart from paid sampling hands), leaving approximately 12.5 ETH. A sixth correct spin credits 18 ETH, but `cashOut()` then pays at most what remains. This accounting is essential to a reproducible draining strategy; the complete historical sequence and generator-state recovery are not archived.

## 5. Observability and test plan

The contract exposes:

```solidity
event HandDealt(uint256 indexed id, address indexed player, uint8[6] cards, uint256 payout);
event SpinSettled(uint256 indexed id, address indexed player, uint8 result, uint256 payout);
function getHand(uint256 id) external view returns (uint8[6] memory);
function cashOut() external;
```

A faithful local investigation would:

1. Call `play(count)` with up to 16 paid hands.
2. Wait for `HandDealt` or call `getHand(id)` after `nextUnsettledHand` advances.
3. Invert each set of six cards into a complete 32-bit word using `recover_word`.
4. Collect enough sequential `getrandbits(32)` samples to assess the MT19937 state and position. Track that `randrange(37)` also consumes the generator and may reject values.
5. Predict selected results only when the state model is independently validated against later observed hands.
6. Place bounded roulette bets, allow the backend to settle, then use `cashOut` and check `Setup.isSolved()`.

No standalone step 4–6 log survived in the ZIP. The missing materials were explicitly requested to recover the missing solver and transaction records.

## 6. Independent offline reconstruction of the MT19937 state

The source-derived prediction is now backed by an independently tested reconstruction, not merely a proposal. The additional [`scripts/recover_mt.py`](scripts/recover_mt.py) is **new test code**, not the recovered historical exploit. It uses only the Python standard library and never contacts the competition service.

Python's MT19937 has a 624-word state, and `getrandbits(32)` returns one tempered 32-bit word per blackjack hand. After reversing the base-52 representation for **624 consecutive hands**, the script inverts the four tempering operations:

```text
y ^= y >> 11
y ^= (y << 7)  & 0x9d2c5680
y ^= (y << 15) & 0xefc60000
y ^= y >> 18
```

The recovered 624 untempered words populate `random.Random.setstate((3, state_words + (624,), None))`. Subsequent `randrange(37)` calls use `getrandbits(6)` with rejection sampling; simply computing `getrandbits(32) % 37` **would be wrong**. By using Python's real `randrange(37)` on the cloned generator, the demonstrator models those extra consumed words.

```bash
python3 scripts/recover_mt.py --selftest
python3 scripts/recover_mt.py --hands hands.json --predict 10
```

`hands.json` contains at least 624 ordered rows of six card IDs, e.g. `{"hands": [[c0,c1,c2,c3,c4,c5], ...]}` with numeric IDs in actual files. The built-in test discards 137 random words before taking its sample, then predicts **100 out of 100** upcoming roulette values correctly. A second local test used JSON hand data and matched ten additional predictions. These are **synthetic, independently executed tests**, not invented on-chain history.

In the simplest isolated collection, 624 hands require 39 calls to `play(16)` and a gross stake-and-tip outlay of `624 × 0.011 = 6.864 ETH` before blackjack payouts; the availability of that amount depends on the challenge wallet. The 624 samples must be consecutive **in the dealer's shared RNG stream**. Interleaved roulette spins, other players or new hands after the sample can desynchronize the model. A local clone does not establish that the winning historical session actually used this route.

## 7. Why the recorded outcome is still credible

The supplied `Chal.sol` and `dealer.py` expose a clear shared-randomness weakness, the account statement contains the accepted task flag, and the final task report identifies the challenge as solved. The vulnerability analysis and score equations are independently checkable. What remains unverified is the **exact exploit transcript and original algorithm** used to turn the weak dealer into the accepted flag. We separate those two evidence levels.

## 8. Defense

Use a verifiable unbiased randomness source with each paid action cryptographically committed before customers can react, do not expose shared PRNG state through game events, and do not reuse a single general-purpose PRNG for high-value lottery payouts and public diagnostic game outcomes.

## Original challenge source

The relevant challenge source is now bundled under `assets/`: [`Chal.sol`](assets/Chal.sol), [`Setup.sol`](assets/Setup.sol), [`dealer.py`](assets/dealer.py). These are the shipped contract/backend files; separately presented example solvers must not be confused with original historical exploit files.
