pragma solidity ^0.8.20;

interface IPalReceiver {
    function onPalReceived(uint256 boxId, uint8 rarity) external returns (bytes4);
}

// PlatyPals: buy a mystery box, our backend opens it for you.
// Only 1% of all boxes contain a legendary pal, and we buy those back for a fortune!
contract Chal {
    uint8 public constant COMMON = 0;
    uint8 public constant RARE = 1;
    uint8 public constant EPIC = 2;
    uint8 public constant LEGENDARY = 3;

    uint256 public constant BOX_PRICE = 1 ether;
    uint256 public constant LEGENDARY_BUYBACK = 50 ether;

    address public immutable backend;

    mapping(uint256 => address) public boxOwner;
    uint256 public nextBoxId;
    uint256 public nextUnopened;

    mapping(address => mapping(uint8 => uint256)) public pals;

    event BoxOpened(uint256 indexed id, address indexed owner, uint8 rarity);

    constructor(address _backend) payable {
        backend = _backend;
    }

    function buyBox() external payable returns (uint256 id) {
        require(msg.value == BOX_PRICE, "A box costs 1 ether");
        id = nextBoxId++;
        boxOwner[id] = msg.sender;
    }

    // rolls are between 0 and 99
    function openBoxes(uint8[] calldata rolls) external {
        require(msg.sender == backend, "Only backend");
        for (uint256 i = 0; i < rolls.length; i++) {
            require(nextUnopened < nextBoxId, "No unopened boxes");
            uint256 id = nextUnopened++;
            address owner = boxOwner[id];
            uint8 rarity = rolls[i] == 0 ? LEGENDARY : rolls[i] < 10 ? EPIC : rolls[i] < 40 ? RARE : COMMON;
            pals[owner][rarity]++;
            emit BoxOpened(id, owner, rarity);

            // pals can live in smart contract wallets too, make sure they are welcome there
            if (owner.code.length > 0) {
                require(
                    IPalReceiver(owner).onPalReceived(id, rarity) == IPalReceiver.onPalReceived.selector,
                    "Pal not accepted"
                );
            }
        }
    }

    function sellLegendary() external {
        require(pals[msg.sender][LEGENDARY] > 0, "No legendary pal");
        pals[msg.sender][LEGENDARY]--;
        payable(msg.sender).transfer(LEGENDARY_BUYBACK);
    }
}
