# Referral

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Category:** blockchain
- **Difficulty:** 2
- **Materials:** `Proxy.sol`, `Chal.sol`, `Setup.sol`
- **Flags:** 1 accepted (`PP{...}`)

A custom upgradeable proxy and its logic contract disagree about storage layout. A referral update rewrites the proxy's implementation pointer.

**Root cause:** In `Proxy`, slot 0 is `address implementation` and slot 1 is `address admin`. In `Chal`, the first state variable, `address latestReferrer`, also occupies slot 0. The proxy forwards calls with `delegatecall`, so writes from `Chal.deposit(referrer)` occur in the proxy's storage. Setting a nonzero referrer thus changes the implementation address. Unlike an isolated mapping collision, this is a direct slot-zero overwrite with a public function.

[Full solving notes](solve.md).
