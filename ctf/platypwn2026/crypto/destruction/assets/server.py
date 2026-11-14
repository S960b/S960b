from os import getenv
import secrets
import string
from encryption import DES3_GCM


BLOCK_SIZE = 8
MIN_AD_LENGTH = 0
MAX_AD_LENGTH = 2**(17 + 3) # 2^17 blocks of 8 bytes each
MAX_ATTEMPTS = 168
PLAINTEXT_LENGTH = 12
COMMAND = b"give me flag"
secret_key = secrets.token_bytes(3*BLOCK_SIZE)
target_H = DES3_GCM.getH(secret_key)


flag0 = getenv("FLAG0", "").encode()
flag1 = getenv("FLAG1", "").encode()


if flag0 == "" or flag1 == "":
    print("Please set the flags using environment variables!")
    # exit(1)

def encrypt(ad_len, plaintext):
    ad_base = b"".join(
        secrets.choice(string.ascii_letters).encode()
        for _ in range(8)
    )
    associated_data = ad_base.rjust(ad_len, b"\0")
    nonce = secrets.token_bytes(BLOCK_SIZE)
    ciphertext, tag = DES3_GCM.Enc(secret_key, plaintext, associated_data, nonce)
    return nonce.hex() + "," + ciphertext.hex() + "," + associated_data.hex() + "," + tag.hex()


def request_encryption():
    try:
        associated_data_length = int(input("Enter AD length: "))
        plaintext = bytes.fromhex(input(f"Enter {PLAINTEXT_LENGTH}-byte plaintext in hex: "))
    except (ValueError, TypeError):
        print("Invalid length or plaintext.")
        return

    if not (
        MIN_AD_LENGTH <= associated_data_length <= MAX_AD_LENGTH
        and associated_data_length % 8 == 0
    ):
        print(f"AD length must be a multiple of 8 from 0 through {MAX_AD_LENGTH}.")
        return
    if len(plaintext) != PLAINTEXT_LENGTH:
        print(f"Plaintext must be exactly {PLAINTEXT_LENGTH} bytes.")
        return
    if COMMAND in plaintext:
        print("Plaintext rejected.")
        return

    print(f"Here is a result: {encrypt(associated_data_length + 4, plaintext)}")


def decrypt_and_check(payload_hex):
    try:
        parts = payload_hex.split(",")
        nonce = bytes.fromhex(parts[0])
        ciphertext = bytes.fromhex(parts[1])
        ad = bytes.fromhex(parts[2])
        tag = bytes.fromhex(parts[3])

        decrypted_data = DES3_GCM.Dec(secret_key, ciphertext, tag, ad, nonce)
        if decrypted_data is None:
            return None
        if COMMAND in decrypted_data:
            return flag0
        if target_H in decrypted_data:
            return flag1
    except Exception as e:
        return None
    return None


def main():
    attempts_left = MAX_ATTEMPTS
    while True:
        print("Options:")
        print("1. Request encryption")
        print("2. Submit ciphertext")
        print("3. Exit")

        choice = input("Enter your choice: ")
        
        if choice == "1":
            if attempts_left > 0:
                request_encryption()
                attempts_left -= 1
                print(f"Attempts left: {attempts_left}")
            else:
                print("No more attempts left. The world is DOOMED!")
                break
        elif choice == "2":
            if attempts_left > 0:
                payload = input("Submit your ciphertext in hex: ")
                flag = decrypt_and_check(payload)
                if flag is not None:
                    print(f"Congratulations! Here is flag: {flag.hex()}")
                else:
                    print("Sus.")
                attempts_left -= 1
                print(f"Attempts left: {attempts_left}")
            else:
                print("No more attempts left. The world is DOOMED!")
                break
        elif choice == "3":
            print("Exiting. The world will perish at the hands of DES-GCM.")
            break
        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    main()
