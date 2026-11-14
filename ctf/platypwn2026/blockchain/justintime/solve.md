# Just in Time: deriving the vault password from the deployment block

[По-русски](solve.ru.md)

## 0. Plan

The objective is to withdraw the entire 100 ETH balance from `Chal`. The constructor presents its password as secret, but creates it from `block.timestamp`, `block.number` and `msg.sender` only. All inputs are recoverable from the chain.

We identify `TARGET`, locate its creation block, distinguish the constructor sender from the player's EOA, reproduce the packed Keccak encoding, submit the password, and check `Setup.isSolved()`.

## 1. Source-level weakness

```solidity
constructor() payable {
    uint256 password = uint256(keccak256(
        abi.encodePacked(block.timestamp, block.number, msg.sender)
    ));
    passwordHash = keccak256(abi.encodePacked(password));
}

function withdraw(uint256 password) external {
    require(keccak256(abi.encodePacked(password)) == passwordHash, "Wrong password");
    payable(msg.sender).transfer(address(this).balance);
}
```

`Setup` directly creates the instance:

```solidity
constructor() payable {
    require(msg.value == 100 ether, "Must send 100 ether");
    TARGET = new Chal{value: 100 ether}();
}

function isSolved() public view returns (bool) {
    return address(TARGET).balance == 0;
}
```

The easily overlooked point is that `msg.sender` **inside the Chal constructor** is the **Setup contract**, not the transaction-signing wallet. Using the EOA would generate a different hash.

## 2. Identifying the three public inputs

Query `Setup.TARGET()` to obtain the Chal address. Find the deployment transaction for Setup and its block. Since `Setup.constructor` calls `new Chal` directly, that creation occurs in the same block. Its timestamp and height are the relevant values.

Reproduce Solidity packed encoding precisely:

| Input | Solidity type | Packed length |
|---|---|---|
| `block.timestamp` | `uint256` | 32 bytes, big-endian |
| `block.number` | `uint256` | 32 bytes, big-endian |
| `msg.sender` | `address` | 20 bytes |
| **Total** | | **84 bytes** |

Python's standard `hashlib.sha3_256` is **not** Ethereum Keccak-256. Use `eth_utils.keccak` or the equivalent library implementation.

## 3. Reproduction helper

This small calculation is reconstructed from `Chal.sol`. The original transaction-specific solver was not in the archived bundle.

```python
from eth_utils import keccak

def calculate_password(timestamp: int, block_number: int, setup_address: str) -> int:
    addr = bytes.fromhex(setup_address.removeprefix("0x"))
    assert len(addr) == 20
    packed = timestamp.to_bytes(32, "big") + block_number.to_bytes(32, "big") + addr
    assert len(packed) == 84
    return int.from_bytes(keccak(packed), "big")
```

Optionally simulate `withdraw(password)` via `eth_call` to check it does not revert before broadcasting a state-changing transaction.

## 4. Complete call chain

```text
Setup.TARGET()
      -> find Chal creation block
      -> (timestamp, block_number, Setup address)
      -> keccak256(84 packed bytes)
      -> Chal.withdraw(password)
      -> Setup.isSolved() == true
```

`withdraw` transfers the **entire** Chal balance. There is no further hidden condition; `Setup.isSolved` only checks the remaining ETH balance.

## 5. Evidence and limitations

The final challenge report confirms an accepted flag (`PP{...}`). The ZIP includes the two original Solidity sources but not the specific deployment address, timestamp, password value, transaction hash or stdout. This walkthrough therefore provides a source-derived reproducible calculation, not an invented historical command transcript.

## 6. Mitigation

Public deployment metadata is not entropy. Hashing a deterministic public tuple may obscure the formula but does not create an unguessable password.

## Original challenge source

The relevant challenge source is now bundled under `assets/`: [`Chal.sol`](assets/Chal.sol), [`Setup.sol`](assets/Setup.sol). These are the shipped contract/backend files; separately presented example solvers must not be confused with original historical exploit files.
