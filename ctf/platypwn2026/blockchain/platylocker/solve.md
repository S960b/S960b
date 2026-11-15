# PlatyLocker: the RSA private exponent sits in a public blockchain

[Русский](solve.ru.md)

## 0. Goal

`Setup` deploys `Chal` and checks a single flag, `filesRestored`. Inside `Chal` there is `payRansom()` asking for **1000 ETH**, which nobody can pay, but the flag is not set there:

```solidity
function restoreFiles(bytes32 key) external {
    require(keccak256(abi.encodePacked(key)) == keyHash, "Wrong key");
    filesRestored = true;
}
```

So we need the 32-byte AES key of the victim. No ransom: the key has to be read, not paid for.

## 1. How the stand creates its secrets (`service/locker_operator.py`)

```python
e = 65537
p, q = getPrime(512), getPrime(512)
n = p * q
d = inverse(e, (p - 1) * (q - 1))
aes_key = os.urandom(32)
c = pow(int.from_bytes(aes_key, "big"), e, n)
chal.functions.initialize(to_bytes(n), e.to_bytes(3, "big"), to_bytes(d),
                          to_bytes(c), Web3.keccak(aes_key))
```

`d` is passed as an argument of an external call. Ethereum calldata is public forever, and the contract also stores `d` in its own storage. The secret is exposed twice.

## 2. Where it is stored

`operator` is `immutable` and `RANSOM` is `constant`: neither takes a storage slot. Layout starts with the `bytes` fields:

| Slot | Variable | Type |
|---:|---|---|
| 0 | `modulus` | `bytes` (128 B) |
| 1 | `publicExponent` | `bytes` (3 B) |
| 2 | `privateExponent` | `bytes` (128 B), marked `private` |
| 3 | `encryptedKey` | `bytes` (128 B) |
| 4 | `keyHash` | `bytes32` |
| 5 | `filesRestored` | `bool` |

EVM rules: for `bytes` longer than 31 bytes the slot holds `2*len+1` and the data lives at `keccak256(slot)`; short values (like `publicExponent`) are packed into the slot itself.

Two equivalent ways to get the secret:

1. read storage slots with `eth_getStorageAt` (slot 2 and the blocks after `keccak256(2)`);
2. decode the calldata of the `initialize` transaction (selector plus four `bytes` arguments; `d` is the third one).

## 3. Steps

```python
setup = w3.eth.contract(address=SETUP, abi=SETUP_ABI)
target = setup.functions.TARGET().call()

def read_bytes(slot):
    s = int.from_bytes(w3.eth.get_storage_at(target, slot), "big")
    if s == 0:
        return b""
    if s & 1:
        return s.to_bytes(32, "big")[:(s - 1) // 2]
    base = int.from_bytes(Web3.keccak(slot.to_bytes(32, "big")), "big")
    body = b"".join(w3.eth.get_storage_at(target, base + i) for i in range((s // 2 + 31) // 32))
    return body[:s // 2]

n, d, c, h = read_bytes(0), read_bytes(2), read_bytes(3), read_bytes(4)
key = pow(int.from_bytes(c, "big"), int.from_bytes(d, "big"),
          int.from_bytes(n, "big")).to_bytes(32, "big")
assert Web3.keccak(key) == h
```

Then call `restoreFiles(key)` and `isSolved()` returns true.

## 4. Why 32 bytes

`aes_key` is exactly 32 random bytes and `c = aes_key^e mod n`. Raising `c` to `d` modulo `n` returns the same integer, but it must be serialised with a fixed length: leading zero bytes would be lost otherwise, and `keyHash` would not match.

## 5. This is not broken RSA

Nobody factored `n`. `d` is public because it was put into calldata and into the storage of a public chain. Given `d`, `c^d mod n` is ordinary decryption.

## 6. Flag

`PP{pr1v4t3_15_n0t_s…c8Gu}`

## 7. Defence

There is no secret storage on a public chain: `private` limits Solidity access only, it does not hide data from RPC or from calldata. Secret components (RSA `d`, AES keys) must stay off-chain; a contract may store a commitment such as `keccak256(key)`.

## 8. What is preserved

- `assets/`: `Chal.sol`, `Setup.sol`, `locker_operator.py`.
- `scripts/exploit.py`: reads slots 0-4, derives the key, verifies `keyHash`, calls `restoreFiles`. RPC URL, private key and `Setup` address are placeholders.
- Not preserved: the concrete `n`, `d`, `c` of the live instance and the transaction hash — the instance is gone, so the script reads everything from the stand instead of replaying numbers.
