pragma solidity ^0.8.20;

contract Chal {
    // Only the hash is stored, the password itself was never written down anywhere.
    bytes32 private immutable passwordHash;

    constructor() payable {
        // A fresh password, generated at the exact moment the vault was created.
        // Not even we know it!
        uint256 password = uint256(keccak256(abi.encodePacked(block.timestamp, block.number, msg.sender)));
        passwordHash = keccak256(abi.encodePacked(password));
    }

    receive() external payable {}

    function withdraw(uint256 password) external {
        require(keccak256(abi.encodePacked(password)) == passwordHash, "Wrong password");
        payable(msg.sender).transfer(address(this).balance);
    }

    function getBalance() external view returns (uint256) {
        return address(this).balance;
    }
}
