pragma solidity ^0.8.20;

// PlatyPay: deposit ether, then send it to your friends using human readable amounts like "0.5".
// Transfers are checked and settled by our payment backend.
contract Chal {
    address public immutable backend;

    mapping(address => int256) public balanceOf;
    uint256 public nextTransferId;

    struct Transfer {
        address from;
        address to;
        string amount;
        bool processed;
    }

    mapping(uint256 => Transfer) public transfers;

    event TransferRequested(uint256 indexed id, address indexed from, address indexed to, string amount);
    event TransferProcessed(uint256 indexed id, bool accepted);

    constructor(address _backend) payable {
        backend = _backend;
    }

    function deposit() external payable {
        balanceOf[msg.sender] += int256(msg.value);
    }

    function withdraw(uint256 amount) external {
        require(balanceOf[msg.sender] >= int256(amount), "Insufficient balance");
        balanceOf[msg.sender] -= int256(amount);
        payable(msg.sender).transfer(amount);
    }

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
        emit TransferProcessed(id, accepted);
    }

    function getBalance() external view returns (uint256) {
        return address(this).balance;
    }
}
