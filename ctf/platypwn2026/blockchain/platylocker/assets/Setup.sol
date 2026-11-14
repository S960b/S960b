pragma solidity ^0.8.20;

import "./Chal.sol";

contract Setup {
    Chal public immutable TARGET;

    constructor() payable {
        require(msg.value == 100 ether, "Must send 100 ether");
        // The deployer operates the ransomware and initializes the keys
        TARGET = new Chal(msg.sender);
    }

    function isSolved() public view returns (bool) {
        return TARGET.filesRestored();
    }
}
