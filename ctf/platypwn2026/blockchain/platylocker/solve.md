# PlatyLocker: solving notes

[По-русски](solve.ru.md)

## 1. Challenge

A ransomware-themed contract stores an RSA private exponent in a Solidity `private` variable, confusing access control at source level with secrecy on a public chain.

**Materials:** `Chal.sol`, `Setup.sol`, `locker_operator.py`.

## 2. Analysis

`initialize` saves the RSA modulus, public exponent, **private exponent** and ciphertext in contract storage. The Python operator produces a 1024-bit RSA keypair and a 32-byte AES key, encrypts the AES key with `pow(m,e,n)`, and sends the exponent `d` to the chain. Solidity's `private` keyword controls other contracts' direct access, but storage slots can be read by node RPC. Dynamic bytes occupy hashed storage locations when long.

## 3. Solving sequence

1. Query `TARGET` from Setup. Locate the `privateExponent` storage slot by the Solidity layout; this contract has `modulus`, `publicExponent`, `privateExponent`, `encryptedKey` as dynamic bytes.
2. Use `eth_getStorageAt` to recover the length metadata in the slot and the actual long-byte contents at `keccak256(slot)` and following slots.
3. Read the publicly accessible modulus and ciphertext (via getters or storage). Calculate `m = pow(c,d,n)` with the recovered private exponent and take the 32-byte AES key.
4. Call `restoreFiles(bytes32(key))`. The contract checks `keccak256(key) == keyHash` and marks `filesRestored=true`.
5. `Setup.isSolved()` checks precisely that Boolean.

## 4. Recorded result

The operator source shows fresh RSA parameters and AES key generation; the contract exposes the complete private-key material via persistent storage. The accepted result is recorded in the agent report, though no RPC transcript was packaged.

1 accepted flag(s); full strings masked `PP{...}`.

## 5. Limits

Exact slot reads, concrete RSA parameters and the successful transaction are not attached. The storage layout is reconstructed from Solidity declarations.

## 6. Mitigation

Do not put private cryptographic material on-chain, including in a Solidity `private` variable. Keep keys off-chain and expose only public verification data.
