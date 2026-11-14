# PlatyPals

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Category:** blockchain
- **Difficulty:** 2
- **Materials:** `Chal.sol`, `Setup.sol`, `box_opener.py`; archived successful-run record
- **Flags:** 1 accepted (`PP{...}`)

A mystery-box market seems to award a legendary pal with 1% probability, but a callback lets the buyer reject all non-legendary outcomes.

**Root cause:** `buyBox` charges 1 ETH. The backend calls `openBoxes(rolls)` with each roll drawn by `secrets.randbelow(100)` and retries a transaction when its receipt has `status != 1`. When the box owner is a smart contract, `openBoxes` calls `onPalReceived(id,rarity)`. A revert inside that callback reverts **the entire opening transaction**, including the increment to `nextUnopened`; the box stays pending. Thus the owner can repeatedly reject COMMON/RARE/EPIC rolls until LEGENDARY appears. `sellLegendary` pays 50 ETH.

[Full solving notes](solve.md).
