# MeowSim: solving it without launching the APK

[По-русски](solve.ru.md)

## 0. Plan

The description talks about feeding, petting, healing and observing a quantum cat. My first question was whether the flag depended on playing a long sequence of actions or on a testable condition in code. Static inspection was faster and easier to reproduce than blindly clicking the interface.

## 1. Inspecting the APK

An APK is a ZIP archive. The first pass identifies DEX code and resources, and a Java decompiler such as JADX turns most of the bytecode back into readable control flow.

```bash
file cat.apk
unzip -l cat.apk | head -40
jadx -d out cat.apk
grep -R -nE 'QuantumState|FlagVault|canOpenFlagVault' out/
```

These are the reproducible discovery commands. The method did not depend on running the Android app or having root on a phone.

## 2. Read the condition before guessing actions

The decisive method was `QuantumState.canOpenFlagVault()`. The recorded condition required all of these facts at the same time:

```text
catAlive       == true
catDead        == true
superposition  == true
stability      >= 90
observed       == false
tampered       == false
```

Normally an application would make alive and dead mutually exclusive. Here that contradiction is the clue: the program's actual boolean test matters more than the UI story.

Observation was a trap. Looking inside the box might seem like the last thing to do, but a state with `observed=true` does not pass the vault condition. Likewise, a state marked as tampered is rejected.

## 3. The flag was encoded, not stored as a plain string

Once the condition is satisfied, `FlagVault` derives key material using SHA-256 and XORs it with an embedded 23-byte encrypted value. The output must therefore be reconstructed in the same byte order and with the same key derivation that the DEX uses.

A useful way to audit any such routine is to label each intermediate value:

```text
state fields
stability integer
SHA-256 input bytes
32-byte digest
23 ciphertext bytes
23 recovered plaintext bytes
```

The algorithm is deterministic. XOR is its own inverse: for a ciphertext byte `c` and keystream byte `k`, the plaintext byte is `c ^ k`.

The original ciphertext constants and exact digest input expression are not present in the surviving written transcript. I will not invent them or publish a made-up decryptor that only prints a known string.

## 4. The observed successful state

The valid state was obtained with `stability=92`, while preserving superposition, both truth values for the cat, and the two false flags. Static reconstruction recovered a 23-byte plaintext matching the required `ctfcup{...}` format. It was verified during the challenge and the team submitted MeowSim successfully.

Why 92 matters: the gate says at least 90, but satisfying the gate alone does not tell us whether stability also feeds the key derivation. The recovered plaintext checked out specifically at 92. Brute forcing every integer without looking at code would have obscured that distinction.

## 5. What I learned

The shortest route was to separate game mechanics from the security condition. The story invites observation, but the code explicitly forbids it. The remaining work is ordinary byte-level reversing: reconstruct the hash input, preserve binary encodings, XOR the embedded bytes and verify that the result is sensible.

This writeup records the tested logic and the result without pretending that the original APK constants survived in the chat history.
