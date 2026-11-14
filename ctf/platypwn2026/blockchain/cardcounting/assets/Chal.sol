pragma solidity ^0.8.20;

// PlatyJack casino, the cards and the wheel are handled by our backend.
// Blackjack: every hand costs a 0.005 ether bet plus a 0.006 ether tip for the dealer (tipping is mandatory, our dealers deserve it!).
//   Everybody plays by the house rules: hit below 17, otherwise stand.
//   Cards 0, 2, 4 belong to the player, cards 1, 3, 5 to the dealer. A win pays your bet twice, a push returns it.
// Roulette: bet on a number between 0 and 36, a hit pays 36 times your bet.
contract Chal {
    address public immutable dealer;
    uint256 public constant HAND_BET = 0.005 ether;
    uint256 public constant DEALER_TIP = 0.006 ether;
    uint256 public constant MAX_HANDS = 16;
    uint256 public constant MIN_SPIN_BET = 0.1 ether;
    uint256 public constant MAX_SPIN_BET = 0.5 ether;

    struct Spin {
        address player;
        uint256 bet;
        uint8 number;
        uint8 result;
        bool settled;
    }

    mapping(uint256 => address) public handPlayer;
    mapping(uint256 => uint8[6]) private handCards;
    uint256 public nextHandId;
    uint256 public nextUnsettledHand;

    mapping(uint256 => Spin) public spins;
    uint256 public nextSpinId;
    uint256 public nextUnsettledSpin;

    mapping(address => uint256) public winnings;

    event HandDealt(uint256 indexed id, address indexed player, uint8[6] cards, uint256 payout);
    event SpinSettled(uint256 indexed id, address indexed player, uint8 result, uint256 payout);

    modifier onlyDealer() {
        require(msg.sender == dealer, "Only dealer");
        _;
    }

    constructor(address _dealer) payable {
        dealer = _dealer;
    }

    function play(uint256 count) external payable {
        require(count > 0 && count <= MAX_HANDS, "Invalid number of hands");
        require(msg.value == count * (HAND_BET + DEALER_TIP), "Bet and tip please");
        for (uint256 i = 0; i < count; i++) {
            handPlayer[nextHandId++] = msg.sender;
        }
    }

    function spin(uint8 number) external payable {
        require(number <= 36, "Invalid number");
        require(msg.value >= MIN_SPIN_BET && msg.value <= MAX_SPIN_BET, "Invalid bet");
        spins[nextSpinId++] = Spin(msg.sender, msg.value, number, 0, false);
    }

    function deal(uint8[6][] calldata cards) external onlyDealer {
        for (uint256 i = 0; i < cards.length; i++) {
            require(nextUnsettledHand < nextHandId, "No open hands");
            uint256 id = nextUnsettledHand++;
            handCards[id] = cards[i];
            uint256 player = score(cards[i][0], cards[i][2], cards[i][4]);
            uint256 house = score(cards[i][1], cards[i][3], cards[i][5]);
            uint256 payout;
            if (player <= 21 && (house > 21 || player > house)) payout = 2 * HAND_BET;
            else if (player <= 21 && player == house) payout = HAND_BET;
            winnings[handPlayer[id]] += payout;
            emit HandDealt(id, handPlayer[id], cards[i], payout);
        }
    }

    function settleSpins(uint8[] calldata results) external onlyDealer {
        for (uint256 i = 0; i < results.length; i++) {
            require(nextUnsettledSpin < nextSpinId, "No open spins");
            Spin storage s = spins[nextUnsettledSpin];
            s.result = results[i];
            s.settled = true;
            uint256 payout = s.result == s.number ? 36 * s.bet : 0;
            winnings[s.player] += payout;
            emit SpinSettled(nextUnsettledSpin, s.player, s.result, payout);
            nextUnsettledSpin++;
        }
    }

    function cashOut() external {
        uint256 amount = winnings[msg.sender];
        if (amount > address(this).balance) amount = address(this).balance;
        winnings[msg.sender] = 0;
        payable(msg.sender).transfer(amount);
    }

    // hit below 17, aces count 11 unless that busts the hand
    function score(uint8 a, uint8 b, uint8 c) public pure returns (uint256 sum) {
        uint8[3] memory hand = [a, b, c];
        uint256 aces;
        for (uint256 i = 0; i < 3 && (i < 2 || sum < 17); i++) {
            uint256 rank = hand[i] % 13;
            sum += rank == 0 ? 11 : rank >= 9 ? 10 : rank + 1;
            if (rank == 0) aces++;
            while (sum > 21 && aces > 0) {
                sum -= 10;
                aces--;
            }
        }
    }

    function getHand(uint256 id) external view returns (uint8[6] memory) {
        return handCards[id];
    }

    function getBalance() external view returns (uint256) {
        return address(this).balance;
    }
}
