# Crypto Wallet - writeup (Reverse/Android: BIP39 phrase from what3words, then AES-GCM flag)

## Getting to know the task

The handout is a single Android APK, `crypto_wallet.apk`. The app is a "crypto wallet": it asks for a 12-word recovery phrase, and if the phrase is right, it shows the flag. There is no remote, no network - the whole challenge is one APK.

The first question is always: what does this thing actually do? For an APK that means - look inside.

## How I looked inside (which tools)

- `file crypto_wallet.apk`, `sha256sum` - it is a zip (APKs always are), ~2.9 MB.
- `unzip -l` - the layout. Two things stand out immediately:
  - `assets/bip39_english.txt` - the standard BIP39 word list (2048 words). So the app indeed works with recovery phrases.
  - `lib/arm64-v8a/libwalletverify.so` and `lib/x86_64/libwalletverify.so` - a NATIVE library. Small (4-5 KB), so it is not a big crypto engine - more like a small helper.
- `jadx` (I installed 1.5.3) - decompiles the dex bytecode back to readable Java. This is the main tool for this challenge: `MainActivity` is the whole app logic.
- `nm -D` / `readelf -Ws` on the .so - the exported native symbols (see the native section).
- `objdump -s -j .rodata` on the .so - the constant data (the coordinates).
- `strings` on the .so - quick scan for readable constants.
- Python `zipfile` - to read a specific asset (the word list) without unpacking.

Alternative tools for the same job: `apktool` (resources + smali), `dex2jar` + a Java decompiler, `jeb`, `Ghidra` for the .so. For this challenge jadx + objdump was enough.

## What MainActivity does (the whole flow), and where each piece came from

The decompiled `MainActivity` (jadx, the wallet-recovery method) is one straight line of crypto. Step by step, with the source of each fact:

1. **It builds 4 word-triples in the format `///w1.w2.w3`.** Source: jadx. The decompiled code slices the entered phrase into sublists of 3 words (`subList(3*i, 3*i+3)`) and prefixes each with the string constant `"///"` - both the slicing and the prefix are visible in the decompiled method.

2. **Each triple goes into `NativeVerifier.checkObjectVicinity` together with a coordinate pair; 4 results are collected.** Source: jadx (the method call and its arguments). The coordinate pairs come from `NativeVerifier.targetCoordinates()` - that is where I went to the native side (next section).

3. **The 4 results pass through `NativeVerifier.hasPerfectMatching` - a gate that must return true.** Source: jadx (the call). Whether this gate actually constrains anything, I checked separately - it turned out it never does (native section below).

4. **The 12 words are converted to indices in the BIP39 list and the BIP39 checksum is verified.** Source: jadx (the digest call) + the asset. The app reads `assets/bip39_english.txt` (I read the same file via `zipfile`; it is byte-identical to the official BIP39 list), maps words to indices, takes SHA-256 of the first 16 bytes of entropy and compares the first 4 bits - the standard BIP39 checksum, visible as JCA calls in the decompiled code.

5. **The master seed is derived: PBKDF2-HMAC-SHA512(phrase, salt="mnemonic", 2048).** Source: jadx. The decompiled code calls `PBKDF2WithHmacSHA512` with the iteration count 2048 and the salt string `"mnemonic"` - both are constants in the method.

6. **HMAC-SHA512 with the "Bitcoin seed" string gives the master private key + chain code.** Source: jadx - the constant `"Bitcoin seed"` is in the code; this is the standard BIP32 seed-to-master step.

7. **BIP32 derivation along m/44'/60'/0'/0/0.** Source: jadx. The decompiled code has an int array of indexes: `{0x8000002c, 0x8000003c, 0x80000000, 0, 0}` = hardened 44, hardened 60, hardened 0, then 0, 0 - the standard Ethereum derivation path.

8. **secp256k1 public key, keccak256 of (x || y), last 20 bytes - Ethereum address, compared with a fixed constant.** Source: jadx. The EC operations are JCA calls; the target address `0xdc7d8b278af5f31fed5fe68f50be6d02404fb522` is a string constant compared with `equalsIgnoreCase`.

9. **On match: key = sha256(private_key_bytes + "FortID/cryptocurrency-wallet/flag/v1"), AES-GCM decrypt, print the flag.** Source: jadx - all constants are in the code: the label `FortID/cryptocurrency-wallet/flag/v1`, the nonce `NaY6LHeKYbf9abvi` (base64), the ciphertext `7ue/WCpa...` (base64), the AAD `wallet-recovery`.

So the entire security is: you must know the 12 words that derive to that one address. The flag is encrypted with a key derived from the private key, so there is no shortcut through the AES part - you must find the phrase.

## The native library: what it really is (and how I read it)

- `nm -D lib/x86_64/libwalletverify.so` (or `readelf -Ws`) lists the EXPORTED symbols. Two JNI functions stand out: `targetCoordinates` and `hasPerfectMatching`. This is how I learned the library exports exactly these two, and nothing else of note.
- Cross-check with jadx: the Java class `NativeVerifier` declares two `native` methods with the same names - so the .so exports and the Java declarations map 1:1.
- `objdump -s -j .rodata libwalletverify.so` (after locating the symbols) shows the constant data of `targetCoordinates`: 4 pairs of (latitude, longitude) doubles:
  - (41.99175, -87.889806)  - ORD, Chicago O'Hare airport
  - (33.941589, -118.40853) - LAX, Los Angeles
  - (44.884755, -93.222284) - MSP, Minneapolis-St Paul
  - (40.641311, -73.778139) - JFK, New York

  (I recognized the airports from the coordinates: they are the well-known center points of those airports. The first diagnostic assumption was "LAX first"; the native array order is ORD, LAX, MSP, JFK - and it matters for the word order.)

- **Are these functions real gates? I checked, and no:**
  - `checkObjectVicinity` - decompiled in jadx: its body always returns `new VicinityResult(true, "Vicinity verification is not implemented yet...")`. It is a stub that always says "yes".
  - `hasPerfectMatching` - I analyzed the native implementation (a 4x4 perfect-matching over a fixed permutation table), then searched the whole dex for any call to it: it is NEVER invoked from the dex.

So both native layers are decoration: they gate NOTHING. The decisive part is: what string does `MainActivity` build the triples from?

## The clue: what3words (and where I got this format)

`MainActivity` builds strings like `///w1.w2.w3`. I googled the format ("///" plus three dot-separated words) and found what3words - a geocoding system where every 3x3 m square has a unique `///word.word.word` address. Sources: the Wikipedia article (https://en.wikipedia.org/wiki/What3words) and the official site (https://what3words.com). The app builds exactly this format and checks each triple against a coordinate pair - which is what3words' reverse geocoding.

So the intended flow: take the four airports - get their what3words addresses - the 12 words (4 x 3) ARE the recovery phrase. This is the "get 12 words" clue, hidden in the native library as coordinates.

## Finding the airport addresses (a dead end first: map centers via API)

First I tried the exact map-center cells: I reverse-looked-up the four center coordinates through what3words (a small Python HTTP client against their public reverse-geocoding endpoint; the same result is visible on their map widget). The triples came back for the map-center cells - and when I derived the Ethereum address from them, it did NOT match the target. Dead end: the author used the airports' published OBJECT addresses, not the cell centers.

What turned out to be right: the PUBLIC what3words addresses of the airport objects (published on the airports' pages / what3words):

```text
ORD: ///slot.escape.oven
LAX: ///dice.ticket.path
MSP: ///sport.glue.survey
JFK: ///reason.manual.myself
```

Concatenated in the native array order (ORD, LAX, MSP, JFK - the order `checkObjectVicinity` receives them):

```text
slot escape oven dice ticket path sport glue survey reason manual myself
```

That is the 12-word recovery phrase.

## The verifier: why a script instead of typing it into the UI? (argument)

Honest answer first: if the APK were running on an emulator or a phone, the fastest path would be typing the 12 words into the app UI and reading the flag off the screen. That is valid, and in a real CTF it is often the right move.

Why I still used a script here:

1. **No emulator was available in the environment.** Setting one up (system image download, boot, install the APK) takes longer than writing a 30-line verifier. The script does the same crypto in seconds, fully offline.
2. **The script proves the phrase is right, not just "the app accepted it".** It prints the derived address and the exact match against the target, then the AES-GCM tag check. The UI shows success/flag but not the address; the script's `match=True` is the evidence, and it is also what I would want before submitting.
3. **It rejects BIP39-invalid phrases instantly** - typo protection. A typo in the UI just fails silently.
4. **It leaves a reproducible artifact** - the verifier is the same chain as the app, usable as the writeup's proof and for any candidate phrase later.

So my opinion: UI-first is the fastest human path when the app actually runs; the script was the environment-constrained equivalent that also produces verifiable evidence. If the emulator had been ready, I would have typed the phrase into the UI too.

## Verification (offline, end-to-end)

The verifier (`scripts/wallet_candidate_check.py`, stdlib + `cryptography`) runs the exact same chain as the app: BIP39 checksum - PBKDF2 - "Bitcoin seed" - BIP32 m/44'/60'/0'/0/0 - secp256k1 - keccak256 - compare with `0xdc7d8b...`, and on match decrypts the AES-GCM blob with `sha256(privkey + label)`.

Result:

```text
$ python3 wallet_candidate_check.py --apk crypto_wallet.apk --mnemonic "slot escape oven dice ticket path sport glue survey reason manual myself"
address=0xdc7d8b278af5f31fed5fe68f50be6d02404fb522 match= True
CHALLENGE FLAG: FortID{Jus7_H0DL_70_7h3_M00n_Br0}
```

Exact address match, AES-GCM accepted, flag decrypted. The flag was then submitted on the portal - ACCEPTED.

## Dead ends I tried along the way

1. **Brute force / guessing the phrase.** 12 BIP39 words = 2^132 with checksum filtering. Pointless, and the challenge clearly hides the phrase elsewhere.

2. **Looking for the phrase inside the APK.** Checked strings, resources.arsc (all UI strings: "Enter your 12-word recovery phrase...", "Wallet recovered", etc.), layouts, the zip comment, long dex strings. Nothing: no 12-word cluster, no narrowed word list, no default phrase. The word list in assets is byte-identical to the official BIP39 list.

3. **The exact map-center what3words cells.** Reverse-looked-up the precise center coordinates of ORD/LAX/MSP/JFK via the public what3words endpoint. Got different triples that do not derive to the target address. The correct triples are the published OBJECT addresses of the airports, not the cell centers.

4. **The native perfect-matching as a real gate.** Fully analyzed `hasPerfectMatching` (a 4x4 perfect-matching over a fixed permutation table), then found it is never invoked from the dex, and `checkObjectVicinity` always returns true ("not implemented yet"). Both native layers are decorative.

5. **Decrypting AES without the phrase.** The AES key is `sha256(privkey + label)`; without the private key (i.e. without the phrase) there is no way to compute it, and AES-GCM is authenticated - you cannot even try candidate keys.

## The one-paragraph version

The APK is a BIP39 + BIP32 + Ethereum + AES-GCM chain: 12 words - seed - m/44'/60'/0'/0/0 - address check against a fixed value - AES-GCM flag. The 12 words are not brute-forceable, but the native library stores the coordinates of four airports (ORD, LAX, MSP, JFK), and `MainActivity` builds what3words-style `///w1.w2.w3` triples and feeds them through two decorative native "verification" layers that always pass. The published what3words object addresses of the four airports are (`slot.escape.oven`, `dice.ticket.path`, `sport.glue.survey`, `reason.manual.myself`) - taken in the native order they are the 12-word phrase. A local verifier reproduces the whole chain offline: exact address match, AES-GCM decrypts the flag, the flag is accepted.

## Lessons learned

1. An APK challenge is three layers, and each needs the right tool: dex - jadx, native .so - nm/objdump/strings, resources - apktool/resource table. Do not guess from the UI.
2. When the code path is a one-way crypto chain (phrase - address), the phrase MUST be hidden somewhere in the artifact. Look for the unusual constant, not the obvious string. Coordinates in a .so are a hint in disguise.
3. what3words addresses (`///word.word.word`) can be the "words" of a BIP39-style phrase - a neat crossover between a geolocation API and a wallet format.
4. Native "security" layers can be pure decoration: verify whether the gate is actually called from the dex before trusting it. Here `hasPerfectMatching` was never invoked and `checkObjectVicinity` always returned true.
5. Match the tool to the environment: with a ready emulator, typing the phrase into the UI is the fastest path; without one, a 30-line offline verifier does the same and prints the evidence (`match=True`, the flag). Both are legitimate; the script additionally protects against typos and leaves a reproducible artifact.
6. The exact-cell vs published-object distinction matters: the challenge used the airport OBJECT addresses, not the map-center cells. When a guess fails, try the other interpretation of the same clue.

## Files

- `scripts/wallet_candidate_check.py` - offline verifier (BIP39 - BIP32 - Ethereum - AES-GCM), usage:
  `python3 wallet_candidate_check.py --apk <apk> --mnemonic "<12 words>"`