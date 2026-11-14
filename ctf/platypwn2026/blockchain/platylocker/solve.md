# PlatyLocker: an RSA private exponent stored on a public blockchain

[По-русски](solve.ru.md)

## 0. Objective

The operator initializes the contract with RSA and AES material, and `Setup.isSolved()` only checks `TARGET.filesRestored()`. The apparent legitimate path `payRansom` costs **1000 ETH**, above the ordinary challenge budget. The actual requirement is merely to learn the AES key and call `restoreFiles(bytes32)`.

The contract declares:

```solidity
bytes public modulus;
bytes public publicExponent;
bytes private privateExponent; // private, so only the contract can use it
bytes public encryptedKey;
bytes32 public keyHash;
bool public filesRestored;
```

The word `private` prevents access by name in Solidity source, **not RPC access to the blockchain's persistent storage**.

## 1. Origin of the cryptographic material

The shipped `service/locker_operator.py` generates:

```python
BITS = 1024
e = 65537
p, q = getPrime(BITS // 2), getPrime(BITS // 2)
n = p * q
d = inverse(e, (p - 1) * (q - 1))
aes_key = os.urandom(32)
c = pow(int.from_bytes(aes_key, "big"), e, n)
```

It invokes `initialize(n,e,d,c,keccak256(aes_key))`. Thus the private exponent `d` is **explicitly included in transaction calldata** and assigned to `privateExponent`. Either the initialization transaction or the contract's storage exposes the secret.

## 2. Solidity storage layout

The fields occupy these slots:

| slot | Field |
|---:|---|
| 0 | dynamic `bytes modulus` |
| 1 | dynamic `bytes publicExponent` |
| 2 | dynamic `bytes privateExponent` |
| 3 | dynamic `bytes encryptedKey` |
| 4 | `bytes32 keyHash` |
| 5 | packed `bool filesRestored` |

The `operator` is `immutable` and does not consume a regular storage slot.

Long dynamic `bytes` data is stored starting at the Keccak-256 hash of the 32-byte slot index; the slot itself contains `2*length+1`. The approximately 128-byte RSA exponent is necessarily in long form.

```python
from eth_utils import keccak

slot = 2
start = int.from_bytes(keccak(slot.to_bytes(32, "big")), "big")
# eth_getStorageAt(TARGET, hex(start + i), "latest") for 4 consecutive words
```

The reads are native Ethereum state inspection, not an application-specific bypass. Parsing the `initialize` transaction parameters is another valid approach.

## 3. Decrypting without paying ransom

Read `n`, `d` and ciphertext `c` as big-endian integers:

```python
m = pow(int.from_bytes(c, "big"),
        int.from_bytes(d, "big"),
        int.from_bytes(n, "big"))
key = m.to_bytes(32, "big")
```

The explicit 32-byte conversion is important: `os.urandom(32)` may start with zero bytes, which would be lost by a minimal-length integer encoding.

`restoreFiles` checks the hash and sets the winning Boolean:

```solidity
function restoreFiles(bytes32 key) external {
    require(keccak256(abi.encodePacked(key)) == keyHash, "Wrong key");
    filesRestored = true;
}
```

No payment is required for that method, and the `Setup` win condition is just `filesRestored == true`.

## 4. Why this is not RSA cryptanalysis

Nobody needs to factor `n`. The private exponent `d` is supplied directly to the publicly readable chain. Once `d` is known, computing `c^d mod n` is ordinary RSA decryption. The vulnerability is disclosure by storage and calldata, not a mathematical weakness of modular exponentiation.

## 5. Evidence and limits

The ZIP supplies `Chal.sol`, `Setup.sol`, `locker_operator.py`, and a platform submission command for an accepted flag. It does not include the exact live parameters or final transaction hash, so no historical RPC transcript is fabricated here.

## 6. Mitigation

Public-chain storage is never secret, regardless of `private` source annotations. Keep RSA private keys and AES keys off-chain; publish only public verification commitments.
