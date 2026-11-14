import os
import sys
import time
import json
import random
import logging
from pathlib import Path

from web3 import Web3
from eth_account import Account

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HTTP_PORT = os.getenv("HTTP_PORT", "8545")
BATCH_SIZE = 16

rng = random.Random()


def load_abi(name: str):
    return json.loads(Path(f"/home/ctf/compiled/{name}.sol/{name}.json").read_text())["abi"]


def deal_hand():
    # shuffling a whole deck for every hand is slow, one random number is plenty for six cards
    number = rng.getrandbits(32)
    cards = []
    for _ in range(6):
        number, card = divmod(number, 52)
        cards.append(card)
    return cards


def spin_wheel():
    return rng.randrange(37)


def send(w3, acct, tx_func):
    tx = tx_func.buildTransaction({
        "from": acct.address,
        "nonce": w3.eth.getTransactionCount(acct.address),
        "gas": 5_000_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": w3.eth.chain_id,
    })
    signed = acct.signTransaction(tx)
    return w3.eth.waitForTransactionReceipt(w3.eth.sendRawTransaction(signed.rawTransaction), timeout=30)


def main():
    uuid, deployer_privkey, setup_addr = sys.argv[1:4]

    acct = Account.from_key(deployer_privkey)
    w3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{HTTP_PORT}/{uuid}"))
    setup = w3.eth.contract(address=Web3.toChecksumAddress(setup_addr), abi=load_abi("Setup"))
    chal = w3.eth.contract(address=setup.functions.TARGET().call(), abi=load_abi("Chal"))
    logging.info("Dealer for %s at table %s", uuid, chal.address)

    while True:
        try:
            open_hands = chal.functions.nextHandId().call() - chal.functions.nextUnsettledHand().call()
            if open_hands > 0:
                cards = [deal_hand() for _ in range(min(open_hands, BATCH_SIZE))]
                send(w3, acct, chal.functions.deal(cards))

            open_spins = chal.functions.nextSpinId().call() - chal.functions.nextUnsettledSpin().call()
            if open_spins > 0:
                results = [spin_wheel() for _ in range(min(open_spins, BATCH_SIZE))]
                send(w3, acct, chal.functions.settleSpins(results))
                logging.info("Spun the wheel: %s", results)

            if open_hands > 0 or open_spins > 0:
                continue
        except Exception:
            logging.exception("Error while dealing")
            time.sleep(3)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
