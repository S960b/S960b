import os
import sys
import time
import json
import logging
from pathlib import Path

from web3 import Web3
from eth_account import Account

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HTTP_PORT = os.getenv("HTTP_PORT", "8545")


def load_abi(name: str):
    return json.loads(Path(f"/home/ctf/compiled/{name}.sol/{name}.json").read_text())["abi"]


def check_transfer(balance: float, amount: str):
    """Returns the amount in ether if the transfer is valid, otherwise None"""
    try:
        value = float(amount)
    except ValueError:
        return None
    if not value > 0:
        return None
    try:
        # no overdrafts!
        if balance < int(value):
            return None
    except (ValueError, OverflowError):
        return None
    return value


def main():
    uuid, deployer_privkey, setup_addr = sys.argv[1:4]

    acct = Account.from_key(deployer_privkey)
    w3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{HTTP_PORT}/{uuid}"))
    setup = w3.eth.contract(address=Web3.toChecksumAddress(setup_addr), abi=load_abi("Setup"))
    chal = w3.eth.contract(address=setup.functions.TARGET().call(), abi=load_abi("Chal"))
    logging.info("Payment backend for %s watching %s", uuid, chal.address)

    next_id = 0
    while True:
        try:
            while next_id < chal.functions.nextTransferId().call():
                sender, receiver, amount, processed = chal.functions.transfers(next_id).call()
                if not processed:
                    balance = chal.functions.balanceOf(sender).call() / 10**18
                    value = check_transfer(balance, amount)
                    accepted = value is not None
                    amount_wei = int(value * 10**18) if accepted else 0
                    logging.info("Transfer %s: %s -> %s (%r) accepted=%s", next_id, sender, receiver, amount, accepted)

                    tx = chal.functions.settle(next_id, accepted, amount_wei).buildTransaction({
                        "from": acct.address,
                        "nonce": w3.eth.getTransactionCount(acct.address),
                        "gas": 200_000,
                        "gasPrice": w3.eth.gas_price,
                        "chainId": w3.eth.chain_id,
                    })
                    signed = acct.signTransaction(tx)
                    w3.eth.waitForTransactionReceipt(w3.eth.sendRawTransaction(signed.rawTransaction), timeout=30)
                next_id += 1
        except Exception:
            logging.exception("Error while processing transfers")
            time.sleep(3)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
