pragma solidity ^0.8.20;

import "./Chal.sol";
import "./Proxy.sol";

contract Setup {
    Chal public immutable TARGET;

    constructor() payable {
        require(msg.value == 100 ether, "Must send 100 ether");
        Proxy proxy = new Proxy(address(new Chal()));
        TARGET = Chal(address(proxy));
        TARGET.deposit{value: 100 ether}(address(0));
    }

    function isSolved() public view returns (bool) {
        return address(TARGET).balance == 0;
    }
}
