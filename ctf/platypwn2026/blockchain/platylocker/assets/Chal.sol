pragma solidity ^0.8.20;

// PlatyLocker v1.3 - ransom payment and key release.
// Running a key server is a hassle and gets taken down all the time,
// so the key is decrypted right here on the blockchain once you pay.
contract Chal {
    address public immutable operator;
    uint256 public constant RANSOM = 1000 ether;

    bytes public modulus;
    bytes public publicExponent;
    bytes private privateExponent; // private, so only the contract can use it
    bytes public encryptedKey;     // your AES file key, encrypted with our public key
    bytes32 public keyHash;
    bool public filesRestored;

    event KeyReleased(address indexed victim, bytes32 key);

    constructor(address _operator) {
        operator = _operator;
    }

    function initialize(bytes calldata n, bytes calldata e, bytes calldata d, bytes calldata c, bytes32 h) external {
        require(msg.sender == operator, "Only operator");
        require(modulus.length == 0, "Already initialized");
        modulus = n;
        publicExponent = e;
        privateExponent = d;
        encryptedKey = c;
        keyHash = h;
    }

    function payRansom() external payable returns (bytes32 key) {
        require(msg.value >= RANSOM, "Pay up");
        key = _decrypt();
        emit KeyReleased(msg.sender, key);
    }

    function restoreFiles(bytes32 key) external {
        require(keccak256(abi.encodePacked(key)) == keyHash, "Wrong key");
        filesRestored = true;
    }

    // RSA decryption: key = encryptedKey ^ privateExponent mod modulus (via the modexp precompile)
    function _decrypt() internal view returns (bytes32 key) {
        bytes memory input = abi.encodePacked(
            encryptedKey.length, privateExponent.length, modulus.length,
            encryptedKey, privateExponent, modulus
        );
        (bool ok, bytes memory result) = address(0x05).staticcall(input);
        require(ok, "modexp failed");
        // the AES key is in the lowest 32 bytes
        assembly {
            key := mload(add(result, mload(result)))
        }
    }
}
