#!/usr/bin/env python3

from os import getenv
import sys
from Crypto.Random import get_random_bytes
from Crypto.Util.number import getPrime

from Crypto.Util.number import bytes_to_long as b2l

flag0 = getenv("FLAG0", "")
flag1 = getenv("FLAG1", "")


if flag0 == "" or flag1 == "":
    print("Please set the flags using environment variables!")
    exit(1)

BITLEN = 1337
DEBUG = False

e = 17  # efficiency and security are not mutually exclusive!



p, q, N, d = None, None, None, None

while d is None:
    try:
        p = getPrime(BITLEN)
        q = getPrime(BITLEN)
        N = p * q
        d = pow(e, -1, (p - 1) * (q - 1))
    except:
        continue

NLEN = (N.bit_length() + 7) // 8
ROUNDS = 256 // e.bit_length() + 1

# High-performance in-memory database
users = {}

# Logging for compliance reasons...
audit_log = []


def gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a

def hash_pw(pw: bytes) -> int:
    h = pow(b2l(pw), e, N)
    return h


# Initial privileged user
admin_secret = get_random_bytes(19)
users["admin"] = hash_pw(admin_secret)


def register():

    try:
        username = input("Username: ")
        username = username.strip()
        password = bytes.fromhex(input("Password(hex): "))

        if username == "admin":
            print("This username is reserved.")
            return

        if not username:
            print("Username cannot be empty!")
            return

        # check whether password contains one lowercase letter, one uppercase letter, one digit, and one special character
        pw = str(password)
        if (
            len(password) != NLEN
            or not any(c.islower() for c in pw)
            or not any(c.isupper() for c in pw)
            or not any(c.isdigit() for c in pw)
            or not any(c in "!@#$%^&*()-+" for c in pw)
        ):
            print(
                f"Password must be exactly {NLEN} bytes long. We have a strong password policy!"
            )
            print(
                "Password must contain at least one lowercase letter, one uppercase letter, one digit, and one special character."
            )
            return

        pw_hash = hash_pw(password)

        # No duplicates!
        for old_username, old_password, old_pw_hash in audit_log:
            if username == old_username:
                print("ERROR: Username already taken. Impersonation is not nice!")
                return
            if old_password == password:
                print(
                    "ERROR: Password already taken. Please show more creativity and invent your own password!"
                )
                return
            # Handle unexpected database storage optimizations
            if pw_hash == old_pw_hash:
                print(
                    "ERROR: Something happened. It was probably a pentest. Have this for now:"
                )
                print(flag0)
                if DEBUG:
                    print(users)

        users[username] = pw_hash
        audit_log.append((username, password, pw_hash))

        print(f"Registered {username!r}.")

    except ValueError:
        print(
            "Invalid input. Please provide a valid plaintext username and password in hex format."
        )
        return


def login():
    try:
        username = input("Username: ").strip()
        pw_hash = users.get(username)
        if pw_hash is None:
            print("User not found.")
            return False

        print(f"Authenticating {username!r} with committed pw_hash {pw_hash}...")

        for rnd in range(ROUNDS):
            print(f"Round {rnd + 1} of {ROUNDS}")

            announcement = int(input("Announcement(hex): "), 16)
            chal = b2l(get_random_bytes(42)) % e
            print(f"Challenge: {chal}")
            response = int(input("Response(hex): "), 16)

            if (response % N) == 0 or (announcement % N) == 0 or gcd(response, N) != 1 or gcd(announcement, N) != 1:
                print("Invalid credentials.")
                return False

            if pow(response, e, N) != (announcement * pow(pw_hash, chal, N)) % N:
                print("Invalid credentials.")
                return False

        print(f"Welcome, {username}!")

        if username == "admin":
            print(f"Here is your flag: {flag1}")

        return True
    except Exception:
        print(f"An error occurred, login failed.")


def main():
    print("Try our new zero-knowledge server authentication!")
    print(f"Parameters: N={N:#x}, e={e:#x}")

    while True:
        print("\n1) Register")
        print("2) Login")
        print("3) Exit")

        choice = input("> ")

        if choice == "1":
            register()
        elif choice == "2":
            result = login()
            if not result and not DEBUG:
                print("MITM detected. Shutting down server for security reasons.")
                exit(1)

        elif choice == "3":
            break
        else:
            print("Invalid option.")


if __name__ == "__main__":
    main()
