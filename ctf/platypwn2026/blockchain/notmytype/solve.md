# Not My Type: solving notes

[По-русски](solve.ru.md)

## 1. Challenge

A payment service accepts human-readable ether amounts; the off-chain validation converts them to incompatible numeric types.

**Materials:** `Chal.sol`, `Setup.sol`, `payment_backend.py`.

## 2. Analysis

`check_transfer(balance, amount)` converts the string to `float`, requires it to be positive, then checks `if balance < int(value)`. For a fractional positive value below 1, `int(value)` is zero. Even an account with zero ETH may therefore pass validation. The backend later settles the **untruncated** float via `int(value * 10**18)`, while the Solidity contract uses signed `int256` account balances and does not repeat the sufficient-funds check in `settle`.

## 3. Solving sequence

1. Create or control two accounts: a sender and a receiver. Request transfers of a positive fractional amount, for example the string `"0.9"`, while the sender has zero internal balance.
2. The backend validates `balance < int(0.9)` as `0 < 0`, which is false, and approves the transfer.
3. `settle` subtracts 0.9 ETH from the sender's signed balance (negative balances allowed) and credits 0.9 ETH to the receiver.
4. Call `withdraw` from the receiver to turn the created internal credit into actual ETH. Repeat the credited-transfer technique until target balance falls below 80 ETH (the Setup threshold).
5. Check `isSolved()`.

## 4. Recorded result

Every part of the rounding/type mismatch appears in the provided `payment_backend.py` and `Chal.sol`: float parsing, integer truncation, signed balances, and missing on-chain settlement validation. The final report records acceptance.

1 accepted flag(s); full strings masked `PP{...}`.

## 5. Limits

The saved evidence only contains the platform submission command, not transaction hashes or number of repetitions. The arithmetic sequence is reconstructed from source.

## 6. Mitigation

Use integer wei or fixed-point decimal types end-to-end. Never settle an amount based solely on off-chain float validation, and enforce nonnegative sender balance in the contract.
