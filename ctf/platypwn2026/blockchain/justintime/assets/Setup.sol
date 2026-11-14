pragma solidity ^0.8.20;

import "./Chal.sol";

contract Setup {
    Chal public immutable TARGET;

    constructor() payable {
        require(msg.value == 100 ether, "Must send 100 ether");
        TARGET = new Chal{value: 100 ether}();
    }

    function isSolved() public view returns (bool) {
        return address(TARGET).balance == 0;
    }
}
