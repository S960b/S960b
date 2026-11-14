# Just in Time

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Category:** blockchain
- **Difficulty:** 1
- **Materials:** Solidity `Chal.sol`, `Setup.sol`
- **Flags:** 1 accepted (`PP{...}`)

A vault claims its password was never written down; it is actually derived entirely from public deployment data.

**Root cause:** The constructor computes `password = uint256(keccak256(abi.encodePacked(block.timestamp, block.number, msg.sender)))` and stores only `keccak256(abi.encodePacked(password))`. The deployer of `Chal` is the `Setup` contract, hence `msg.sender` in that constructor is the Setup address, not the player's EOA. The timestamp and block number are public in the deployment block. The password is therefore deterministic and not secret.

[Full solving notes](solve.md).
