pragma solidity ^0.8.20;

// PlatyVault v2: now with a referral program!
contract Chal {
    // shout-out to whoever brought in our latest customer
    address public latestReferrer;
    mapping(address => uint256) public balances;
    mapping(address => uint256) public referrals;

    function deposit(address referrer) external payable {
        require(referrer != msg.sender, "No self referrals");
        balances[msg.sender] += msg.value;
        if (referrer != address(0)) {
            referrals[referrer]++;
            latestReferrer = referrer;
        }
    }

    function withdraw(uint256 amount) external {
        require(balances[msg.sender] >= amount, "Insufficient balance");
        balances[msg.sender] -= amount;
        payable(msg.sender).transfer(amount);
    }

    function getBalance() external view returns (uint256) {
        return address(this).balance;
    }
}
