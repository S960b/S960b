import os
import sys
import json
import logging
from pathlib import Path

from Crypto.Util.number import getPrime, inverse
from web3 import Web3
from eth_account import Account

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HTTP_PORT = os.getenv("HTTP_PORT", "8545")
BITS = 1024


def load_abi(name: str):
    return json.loads(Path(f"/home/ctf/compiled/{name}.sol/{name}.json").read_text())["abi"]


def to_bytes(x: int) -> bytes:
    return x.to_bytes(BITS // 8, "big")


def main():
    uuid, deployer_privkey, setup_addr = sys.argv[1:4]

    acct = Account.from_key(deployer_privkey)
    w3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{HTTP_PORT}/{uuid}"))
    setup = w3.eth.contract(address=Web3.toChecksumAddress(setup_addr), abi=load_abi("Setup"))
    chal = w3.eth.contract(address=setup.functions.TARGET().call(), abi=load_abi("Chal"))

    # fresh RSA key pair and AES file key for every victim
    e = 65537
    while True:
        p, q = getPrime(BITS // 2), getPrime(BITS // 2)
        phi = (p - 1) * (q - 1)
        if phi % e != 0:
            break
    n = p * q
    d = inverse(e, phi)
    aes_key = os.urandom(32)
    c = pow(int.from_bytes(aes_key, "big"), e, n)

    tx = chal.functions.initialize(
        to_bytes(n), e.to_bytes(3, "big"), to_bytes(d), to_bytes(c), Web3.keccak(aes_key)
    ).buildTransaction({
        "from": acct.address,
        "nonce": w3.eth.getTransactionCount(acct.address),
        "gas": 2_000_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": w3.eth.chain_id,
    })
    signed = acct.signTransaction(tx)
    receipt = w3.eth.waitForTransactionReceipt(w3.eth.sendRawTransaction(signed.rawTransaction), timeout=30)
    logging.info("Victim %s initialized (status=%s)", chal.address, receipt.status)


if __name__ == "__main__":
    main()
