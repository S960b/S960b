# Crypto Wallet - BIP39 recovery phrase from what3words airport addresses, then AES-GCM

- **Platform:** FortiD CTF 2026
- **Category:** Reverse (Android)
- **Author:** (author not recorded in the workspace)
- **Points / Solves:** (points/solves for this one were not recorded in the workspace)
- **Date:** 2026-10-10
- **Remote:** none (an Android APK, fully offline challenge)
- **Flag:** `FortID{...}` (masked)
- **APK:** `crypto_wallet.apk` (sha256 `77806c2604613ee435a5d77841d5f2968da638f8ccb87d329fd9d0642f685236`)
- **Description (official):** a "crypto wallet" app that asks for a 12-word recovery phrase and checks it against a fixed Ethereum address

An Android APK. `MainActivity` runs a 12-word recovery phrase through: BIP39 checksum, PBKDF2, "Bitcoin seed", BIP32 path m/44'/60'/0'/0/0, secp256k1 pubkey, keccak256, comparison with the fixed address `0xdc7d8b278af5f31fed5fe68f50be6d02404fb522`; on match it AES-GCM-decrypts the flag with a key derived from the private key. The 12 words are not brute-forceable (2^132 space), but the native library `libwalletverify.so` stores 4 airport coordinates (ORD, LAX, MSP, JFK), and `MainActivity` builds `///w1.w2.w3` triples - the what3words format. The airport object addresses are publicly known: `///slot.escape.oven`, `///dice.ticket.path`, `///sport.glue.survey`, `///reason.manual.myself` - the 12 words of the phrase. Verified locally end-to-end and the flag was accepted. (A verifier script was used because no emulator was available; with one, typing the phrase into the UI would have been the fastest path.)

Files:

- `solve.md` - the full write-up, including the dead ends (English)
- `solve.ru.md` - the same write-up in Russian (working copy)
- `scripts/wallet_candidate_check.py` - offline verifier (BIP39, BIP32, Ethereum, AES-GCM)