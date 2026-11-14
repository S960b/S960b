# Not My Type: float validation versus signed on-chain accounting

[По-русски](solve.ru.md)

## 0. Plan

`PlatyPay` accepts transfers with human-readable ETH amounts such as `"0.5"`. A Python backend validates the amount, then sends a settlement transaction to Solidity. We need to reduce the contract's actual ETH balance from 100 ETH to **below 80 ETH** (`Setup.isSolved`).

The flaw is not a transaction race: the validator truncates the amount to whole ETH for the funds check, but uses the full fractional value for settlement. The contract represents customer balances as **signed** `int256` and does not repeat the sufficient-balance check when receiving settlement results.

We can therefore make the backend create credit for an account without a corresponding deposit, then withdraw that credit from the contract.

## 1. Inspecting the backend's validation

The shipped `service/payment_backend.py` contains:

```python
def check_transfer(balance: float, amount: str):
    try:
        value = float(amount)
    except ValueError:
        return None
    if not value > 0:
        return None
    try:
        if balance < int(value):
            return None
    except (ValueError, OverflowError):
        return None
    return value
```

The backend reads a sender's contract balance and converts from wei to ETH:

```python
balance = chal.functions.balanceOf(sender).call() / 10**18
value = check_transfer(balance, amount)
accepted = value is not None
amount_wei = int(value * 10**18) if accepted else 0
chal.functions.settle(next_id, accepted, amount_wei)
```

For the input `amount="0.9"` and sender balance zero, `int(0.9)=0`, so:

```text
if 0.0 < 0: reject       # condition false
accepted = True
amount_wei = int(0.9 * 10**18) = 900000000000000000
```

The precision hazard of floating point is real, but for this value the intended integer result is stable; the more important logical error is **truncation for validation and a different amount for settlement**.

## 2. Why Solidity lets a negative balance exist

The source declares:

```solidity
mapping(address => int256) public balanceOf;

function transfer(address to, string calldata amount) external returns (uint256 id) {
    id = nextTransferId++;
    transfers[id] = Transfer(msg.sender, to, amount, false);
    emit TransferRequested(id, msg.sender, to, amount);
}

function settle(uint256 id, bool accepted, uint256 amountWei) external {
    require(msg.sender == backend, "Only backend");
    Transfer storage t = transfers[id];
    require(!t.processed, "Already processed");
    t.processed = true;
    if (accepted) {
        balanceOf[t.from] -= int256(amountWei);
        balanceOf[t.to] += int256(amountWei);
    }
}
```

The `settle` function checks that the caller is the trusted backend and that the transfer is not already processed. It does **not** check whether `balanceOf[t.from] >= amountWei`. Since `int256` is signed, subtracting 0.9 ETH from zero is not an underflow; it becomes the legitimate signed value **-0.9 ETH**.

This is where the trust boundary fails: Solidity treats the backend decision as authoritative, even though that decision was based on a truncated number.

## 3. Creating and realizing artificial credit

Prepare two controlled addresses A and B:

1. A holds zero internal PlatyPay balance.
2. A calls `Chal.transfer(B, "0.9")`, producing a `TransferRequested` event and an incremented `nextTransferId`.
3. The backend processes the next transfer ID, checks it against `int(0.9)==0` and calls `settle(id, true, 0.9 ether)`.
4. The internal ledger becomes `balanceOf[A]=-0.9 ETH` and `balanceOf[B]=+0.9 ETH`.
5. B calls `withdraw(0.9 ether)` to turn the artificial internal credit into actual ETH from `Chal`.

The withdrawal method only checks the recipient's positive account balance:

```solidity
function withdraw(uint256 amount) external {
    require(balanceOf[msg.sender] >= int256(amount), "Insufficient balance");
    balanceOf[msg.sender] -= int256(amount);
    payable(msg.sender).transfer(amount);
}
```

The contract starts at 100 ETH. Transferring 0.9 ETH out per round requires **at least 23 rounds**, because 22 rounds remove 19.8 ETH and leave 80.2 ETH, while 23 remove 20.7 ETH and leave 79.3 ETH. The victory condition is strictly `<80 ether`.

### Why this is not free-ether arithmetic

The contract's total internal liabilities still sum to zero across A and B: one balance becomes negative while another becomes positive. The real loss occurs because **only the positive account withdraws**, while the negative one is never required to repay. The contract already owns 100 ETH funded by Setup, so it can pay out despite the ledger mismatch.

## 4. What can be verified from the source

A local or forked test should check these four observations independently: `TransferRequested` exists; `TransferProcessed(...,true)` arrives; `balanceOf(A)` is negative and `balanceOf(B)` positive; after B's withdrawal, `address(Chal).balance` decreases and eventually `Setup.isSolved()` is true.

A minimal interface sequence (not an original captured live script):

```text
transfer(B, "0.9")       from A
wait for TransferProcessed(id, accepted=true)
balanceOf(A) == -0.9e18
balanceOf(B) == +0.9e18
withdraw(0.9e18)        from B
repeat until getBalance() < 80e18
```

The original archive has `Chal.sol` and `payment_backend.py` plus a platform flag-submission command. The actual transaction hashes and original exploit file were **not** in that ZIP. The recorded result confirms the challenge was accepted; the sequence above is verified by source logic, not misrepresented as its verbatim log.

## 5. Dead ends and lessons

- Merely sending a normal integer amount from a zero-balance account is correctly rejected.
- Trying to submit a settlement call from the player address fails `Only backend`; the weakness is getting the backend to make the authorized call.
- Depositing 20 ETH would not reduce the contract balance; it increases the contract's reserves.
- Fixing the Python validator alone is fragile. The **contract** must reject a settlement when the debit would create an unauthorized negative balance.

The correct architecture uses integer wei or fixed-point decimal values end-to-end and repeats the asset-balance invariant in the on-chain settlement method.
