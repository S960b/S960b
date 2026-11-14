# PlatyPals: solving notes

[По-русски](solve.ru.md)

## 1. Challenge

A mystery-box market seems to award a legendary pal with 1% probability, but a callback lets the buyer reject all non-legendary outcomes.

**Materials:** `Chal.sol`, `Setup.sol`, `box_opener.py`; direct Git-bridge success report.

## 2. Analysis

`buyBox` charges 1 ETH. The backend calls `openBoxes(rolls)` with each roll drawn by `secrets.randbelow(100)` and retries a transaction when its receipt has `status != 1`. When the box owner is a smart contract, `openBoxes` calls `onPalReceived(id,rarity)`. A revert inside that callback reverts **the entire opening transaction**, including the increment to `nextUnopened`; the box stays pending. Thus the owner can repeatedly reject COMMON/RARE/EPIC rolls until LEGENDARY appears. `sellLegendary` pays 50 ETH.

## 3. Solving sequence

1. Deploy a `RerollReceiver` implementing `onPalReceived(uint256,uint8)` and make it revert unless `rarity == 3` (LEGENDARY); for rarity 3 return `IPalReceiver.onPalReceived.selector`.
2. Buy exactly one box via this contract using 1 ETH. Do not queue many boxes: one failed callback reverts the batch.
3. Let the backend repeatedly try `openBoxes`. Non-legendary rolls revert and remain pending; the next retry generates new random bytes. Once legendary appears, the callback succeeds and `pals[receiver][3]` increases.
4. Call `sellLegendary` from the receiver contract and repeat with a second box.
5. The recorded session ended with balance `100 + 1 - 50 + 1 - 50 = 2 ETH`, below the 10 ETH threshold.

## 4. Recorded result

The direct Git-bridge report records successful deployment, a legendary on approximately the 31st block after purchase, about 330 failed rerolls for the next legendary, two buybacks and the final 2 ETH balance. The on-chain source explains the rollback behavior.

1 accepted flag(s); full strings masked `PP{...}`.

## 5. Limits

The deployed receiver source and transaction hashes are not present in the ZIP. The interface and `require(rarity==3, "reroll")` logic are reconstructed from the agent report.

## 6. Mitigation

Do not let an untrusted receiver callback veto or repeatedly resample randomness without cost. Separate random choice from notification and settle state before external calls, with a safe handling path for failed callbacks.
