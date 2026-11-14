# Not My Type

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Category:** blockchain
- **Difficulty:** 1
- **Materials:** `Chal.sol`, `Setup.sol`, `payment_backend.py`
- **Flags:** 1 accepted (`PP{...}`)

A payment service accepts human-readable ether amounts; the off-chain validation converts them to incompatible numeric types.

**Root cause:** `check_transfer(balance, amount)` converts the string to `float`, requires it to be positive, then checks `if balance < int(value)`. For a fractional positive value below 1, `int(value)` is zero. Even an account with zero ETH may therefore pass validation. The backend later settles the **untruncated** float via `int(value * 10**18)`, while the Solidity contract uses signed `int256` account balances and does not repeat the sufficient-funds check in `settle`.

[Full solving notes](solve.md).
