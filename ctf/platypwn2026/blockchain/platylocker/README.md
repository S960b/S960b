# PlatyLocker

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Category:** blockchain
- **Difficulty:** 1
- **Materials:** `Chal.sol`, `Setup.sol`, `locker_operator.py`
- **Flags:** 1 accepted (`PP{...}`)

A ransomware-themed contract stores an RSA private exponent in a Solidity `private` variable, confusing access control at source level with secrecy on a public chain.

**Root cause:** `initialize` saves the RSA modulus, public exponent, **private exponent** and ciphertext in contract storage. The Python operator produces a 1024-bit RSA keypair and a 32-byte AES key, encrypts the AES key with `pow(m,e,n)`, and sends the exponent `d` to the chain. Solidity's `private` keyword controls other contracts' direct access, but storage slots can be read by node RPC. Dynamic bytes occupy hashed storage locations when long.

[Full solving notes](solve.md).
