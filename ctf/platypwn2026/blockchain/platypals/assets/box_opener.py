import os
import sys
import time
import json
import secrets
import logging
from pathlib import Path

from web3 import Web3
from eth_account import Account

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HTTP_PORT = os.getenv("HTTP_PORT", "8545")
BATCH_SIZE = 16


def load_abi(name: str):
    return json.loads(Path(f"/home/ctf/compiled/{name}.sol/{name}.json").read_text())["abi"]


def main():
    uuid, deployer_privkey, setup_addr = sys.argv[1:4]

    acct = Account.from_key(deployer_privkey)
    w3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{HTTP_PORT}/{uuid}"))
    setup = w3.eth.contract(address=Web3.toChecksumAddress(setup_addr), abi=load_abi("Setup"))
    shop = w3.eth.contract(address=setup.functions.TARGET().call(), abi=load_abi("Chal"))
    logging.info("Box opener for %s at shop %s", uuid, shop.address)

    while True:
        try:
            unopened = shop.functions.nextBoxId().call() - shop.functions.nextUnopened().call()
            if unopened > 0:
                rolls = [secrets.randbelow(100) for _ in range(min(unopened, BATCH_SIZE))]
                tx = shop.functions.openBoxes(rolls).buildTransaction({
                    "from": acct.address,
                    "nonce": w3.eth.getTransactionCount(acct.address),
                    "gas": 5_000_000,
                    "gasPrice": w3.eth.gas_price,
                    "chainId": w3.eth.chain_id,
                })
                signed = acct.signTransaction(tx)
                receipt = w3.eth.waitForTransactionReceipt(w3.eth.sendRawTransaction(signed.rawTransaction), timeout=30)
                if receipt.status != 1:
                    logging.warning("Opening %s boxes failed, trying again", len(rolls))
        except Exception:
            logging.exception("Error while opening boxes")
            time.sleep(3)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
