# MeowSim: the flag behind an impossible state

[По-русски](solve.ru.md)

## 0. Plan

The app is a pocket laboratory with a quantum cat: feed it, pet it, heal it, observe it. The question is whether the flag comes from playing long enough or from satisfying a condition in code. Static analysis answers that faster than clicking.

Tools: an APK is a ZIP, so `unzip -l` first, then a DEX disassembler. On this machine there was no JADX, so the DEX was read with a Python library and disassembled method by method (`androguard`).

Why this step: the whole challenge is a boolean gate plus a decryption routine, and both are readable without running Android.

## 1. The gate

`QuantumState.canOpenFlagVault()` requires six conditions at once:

```text
pet.catAlive        == true
box.catDead         == true
box.superposition   == true
box.stability       >= 90
observer.observed   == false
tampered            == false
```

The first two contradict each other in normal play, and that contradiction is the point. `observed` must stay false, so the obvious move, looking into the box, is a trap.

Why this step: this is the entire acceptance condition; everything after it is bookkeeping about how the state is stored.

## 2. Where the state lives

Four stores, all visible in the DEX:

- `PetStore`, SharedPreferences `pet_state`: `catAlive`, hunger, mood, energy, health, cleanliness.
- `BoxStore`, SQLite `box_state.db`, table `box_state`: `boxClosed`, `catDead`, `superposition`, `stability`, `boxSeal`.
- `ObserverStore`, SharedPreferences `observer_state`: `observed`, `observerToken`, `lastMeasurement`. Default token is the literal `ny4n-lab-token`.
- `StateRepository` glues them into `QuantumState` and exposes the actions.

Two details matter later. `stabilize()` writes `stability = 92`, so 92 is not a magic number invented by the solver, it is the value the app itself sets. And `BoxStore.sealFor()` computes the seal as

```text
sha256_hex("box|" + catDead + "|" + superposition + "|" + stability + "|meowsim-v1")
```

so the seal binds the box fields that the flag key depends on.

## 3. How an impossible state is reachable

`RestoreExperimentActivity` is a lab feature: it accepts an intent with `mode`, `snapshot` and `seal`, verifies the seal through `SealVerifier` (`sha256(observer | box | "restore-v1")`) and restores the box from a snapshot. A snapshot is a small text blob of `key=value` pairs parsed by `ExperimentSnapshot.parse`: `catAlive`, `catDead`, `superposition`, `observed`, `restoreObserverState`, `stability`.

The bug is the word "restore": the box is restored from the snapshot while the observer record is restored separately (or not at all). That decoupling is exactly what allows `catAlive` in the pet record and `catDead` in the box record to be true at the same time, with `observed` still false. `TamperDetector` only checks that the package name is the expected one, so re-signing the APK does not help and is not needed: the restore path is a legitimate in-app feature.

Why this step: without the restore path, the six conditions are mutually exclusive; with it, they are a matter of which store you write.

## 4. The flag is encrypted, not stored

`FlagVault` keeps a 23-byte array (`ENCRYPTED_FLAG`) and derives the key from the state:

```text
key = sha256(observerToken | boxSeal | stability | catAlive | catDead |
             superposition | observed | "meowsim-v1")
```

The keystream is the concatenation of `sha256(key || be32(counter))` blocks, truncated to 23 bytes, and the plaintext is the XOR of the array with that stream. The result is accepted only if it starts with `ctfcup{`.

So the flag cannot be found with `strings`; it has to be reconstructed with the correct state values, byte order and key derivation. `scripts/decrypt_flag.py` does exactly this with the defaults: token `ny4n-lab-token`, `observed=false`, cat alive and dead, superposition on, stability 92.

Why this step: the encrypted array alone is useless, and the state alone gives no text; the two together are the whole solution.

## 5. Result

With the state above the decryption returns 23 readable bytes in the required format, and the challenge was submitted. The flag is masked here as `ctfcup{...}`.

```bash
python3 scripts/decrypt_flag.py                 # default state, stability 92
python3 scripts/decrypt_flag.py --observed      # sanity check: rejected
```

The second form fails on purpose, which shows that `observed=true` is rejected by the gate rather than silently producing garbage that looks like a flag.

## 6. Lessons

- Separate the game logic from the security condition; the story suggests observing, the code forbids it.
- Look for in-app "restore" and "import" features: they often decouple fields that normal flow keeps consistent.
- When a flag is encrypted with a key derived from state, the writeup must state the exact concatenation order and the exact byte format, otherwise it is not reproducible.

One paragraph: the flag vault opens only for a state where the pet is alive, the box says dead and in superposition, stability is at least 90, and the observer has not measured anything; the lab restore feature lets the box be restored from a snapshot independently of the observer, `stabilize()` supplies stability 92, and the 23-byte encrypted array is decrypted with a SHA-256 key over that state and an XOR keystream of hashed counters.
