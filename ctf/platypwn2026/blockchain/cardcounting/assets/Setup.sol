pragma solidity ^0.8.20;

import "./Chal.sol";

contract Setup {
    Chal public immutable TARGET;

    constructor() payable {
        require(msg.value == 100 ether, "Must send 100 ether");
        // The deployer runs the dealer backend
        TARGET = new Chal{value: 100 ether}(msg.sender);
    }

    function isSolved() public view returns (bool) {
        return address(TARGET).balance < 10 ether;
    }
}
