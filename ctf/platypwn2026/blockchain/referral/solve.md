# Referral: storage-layout collision overwrites an upgradeable proxy

[По-русски](solve.ru.md)

## 0. Plan

`PlatyVault v2` adds a referral field to a deposit/withdrawal contract and runs it behind a home-made `Proxy`. The Setup deploys a proxy with 100 ETH and considers the challenge solved only if its balance reaches **zero**. A player cannot withdraw the deployer's deposited 100 ETH through the normal `balances` mapping.

The route is not a referral reward bug. The first field of the logic contract **collides with the proxy's implementation pointer** under `delegatecall`. We can overwrite the pointer to our own implementation and make the proxy execute a function that drains itself.

## 1. Two incompatible storage layouts

The proxy defines:

```solidity
contract Proxy {
    address public implementation;  // storage slot 0
    address public admin;           // storage slot 1

    function upgradeTo(address newImplementation) external {
        require(msg.sender == admin, "Only admin");
        implementation = newImplementation;
    }

    fallback() external payable {
        address impl = implementation;
        assembly {
            calldatacopy(0, 0, calldatasize())
            let ok := delegatecall(gas(), impl, 0, calldatasize(), 0, 0)
            returndatacopy(0, 0, returndatasize())
            switch ok
            case 0 { revert(0, returndatasize()) }
            default { return(0, returndatasize()) }
        }
    }
}
```

The implementation `Chal` instead begins with:

```solidity
address public latestReferrer;   // slot 0
mapping(address => uint256) public balances; // slot 1 mapping root
mapping(address => uint256) public referrals; // slot 2 mapping root
```

Calling implementation code through `delegatecall` executes it with the **proxy's storage, address and ETH balance**. Therefore a write to `Chal.latestReferrer` is a write to `Proxy.implementation`, both at slot 0.

## 2. How to reach the write

The publicly callable function is:

```solidity
function deposit(address referrer) external payable {
    require(referrer != msg.sender, "No self referrals");
    balances[msg.sender] += msg.value;
    if (referrer != address(0)) {
        referrals[referrer]++;
        latestReferrer = referrer;
    }
}
```

Three important details:

- The guard prevents referring to **your own address**, but does not require the referee to be registered or trusted.
- `referrer != address(0)` makes the write happen.
- The untrusted `referrer` address becomes the value of `implementation` in the proxy's storage.

Deploy an attacker-controlled **implementation contract** at address `E` and call `deposit(E)` via the proxy from another address `A != E`. There is no minimum amount: `msg.value=0` suffices to reach the assignment. The own-address restriction is satisfied because `E` is a separate contract.

Before calling, it is useful to read `Proxy.implementation()` (if the typed ABI matches); afterward that getter will report `E`. A read of `eth_getStorageAt(proxy,0)` would show the same pointer.

## 3. What the new implementation needs to do

A minimal function illustrates the required semantics:

```solidity
contract Drain {
    function drain(address payable recipient) external {
        recipient.transfer(address(this).balance);
    }
}
```

This contract is not a historical recovered attacker file; it is the minimal form of the delegation effect from the source. **Invoke it at the proxy's address**, using the Drain ABI, not at the `Drain` contract's own address:

```text
proxy (storage slot 0 = E)
   fallback selector drain(address)
     -> delegatecall E
        -> address(this) == proxy
        -> address(this).balance == 100 ether
        -> transfer balance to recipient
```

The proxy does not become the implementation's own account. `delegatecall` keeps the proxy as the execution/storage context. Thus `Drain.drain` can send the proxy's 100 ETH even though `Chal.withdraw` would check its own mapping.

## 4. The exact win condition

Setup's constructor:

```solidity
Proxy proxy = new Proxy(address(new Chal()));
TARGET = Chal(address(proxy));
TARGET.deposit{value: 100 ether}(address(0));
```

Because the initial `referrer` is zero, this deposit does **not** overwrite the implementation, and the proxy still points to legitimate Chal after setup. The 100 ETH are owned by the proxy but recorded in the `balances[Setup]` mapping; the player's account has none.

The malicious referral call replaces the implementation pointer, then a delegated withdrawal drops the proxy's balance to zero. `Setup.isSolved()` evaluates `address(TARGET).balance == 0`, confirming the goal.

## 5. Evidence and limitations

The supplied `Proxy.sol`, `Chal.sol` and `Setup.sol` prove every slot assignment and the delegatecall behavior. The agent's list confirms an accepted flag. The actual malicious contract bytecode, calldata and deployment hashes are **not included** in the archive. The Drain example is a reproducible demonstration, not a fabricated original artifact.

## 6. Mitigation

Reserve the proxy implementation slot using EIP-1967/UUPS and verify storage-layout compatibility for upgrades. A proxy should never share slot 0 with an implementation's ordinary business field. Restrict upgrades; avoid trusting unvalidated referral addresses as values that can ever influence code selection.
