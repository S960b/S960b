# Just in Time: solving notes

[По-русски](solve.ru.md)

## 1. Challenge

A vault claims its password was never written down; it is actually derived entirely from public deployment data.

**Materials:** Solidity `Chal.sol`, `Setup.sol`.

## 2. Analysis

The constructor computes `password = uint256(keccak256(abi.encodePacked(block.timestamp, block.number, msg.sender)))` and stores only `keccak256(abi.encodePacked(password))`. The deployer of `Chal` is the `Setup` contract, hence `msg.sender` in that constructor is the Setup address, not the player's EOA. The timestamp and block number are public in the deployment block. The password is therefore deterministic and not secret.

## 3. Solving sequence

1. Query `Setup.TARGET()` and find the transaction and block that created the target `Chal` contract.
2. Read the block's timestamp and number. Use the Setup contract address for `msg.sender`.
3. Recreate Solidity packed encoding of two `uint256` values (32 bytes each) and one address (20 bytes); calculate Keccak-256 and interpret the result as uint256.
4. Call `TARGET.withdraw(password)` from the player account. This transfers the entire challenge balance to the caller.
5. Verify that `Setup.isSolved()` returns true (`TARGET` balance is zero).

## 4. Recorded result

Both formulas and the zero-balance solve condition are explicit in the attached Solidity source. The final task report records an accepted flag. Transaction hash and exact recovered password were not included.

1 accepted flag(s); full strings masked `PP{...}`.

## 5. Limits

The saved materials contain source but no deployed address, block identifier or actual withdraw transaction. The reproduction steps are derived directly from that code.

## 6. Mitigation

Do not use block time, block number or sender address as a secret. Derive secrets off-chain with unpredictable randomness and avoid publishing a checkable secret-dependent predicate on chain.
