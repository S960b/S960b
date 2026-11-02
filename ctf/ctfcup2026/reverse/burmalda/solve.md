# Burmalda: the flag is in the client package

[По-русски](solve.ru.md)

## 0. What the challenge looks like

The instance serves a browser game: a roulette with a balance, a bet field, red and black buttons, and a separate panel that is supposed to show the flag. The obvious reading is "win the game", and that reading is wrong.

The page is a Godot 4.4.1 HTML5 export. A browser cannot run a Godot game without downloading the whole thing, so everything the game needs, including its scripts, arrives on the client. The question is what those scripts contain.

Why this step: recognising the engine immediately changes the target. A game exported to the web is a static bundle, not a server-side service.

## 1. Getting the package

The export loads a `.pck` file, the Godot resource package. It is listed in the network panel as `/Burmalda.pck` and can be downloaded directly:

```bash
curl -O https://<host>/Burmalda.pck
sha256sum Burmalda.pck
```

The file is 43 760 bytes and starts with the `GDPC` signature, format version 2.

The game tries to make inspection awkward: it swallows `F12` with its own keyboard handler. That is cosmetic, since the browser menu still opens developer tools and a separate Chrome profile with `--auto-open-devtools-for-tabs` sidesteps the page entirely.

Why this step: the `F12` block suggests the authors expected someone to look at the client, and the package is where the answer lives.

## 2. Recovering the scripts

`GDRE Tools` (the `gdsdecomp` project) unpacks a PCK and decompiles GDScript bytecode:

```bash
gdre_tools --headless --recover=Burmalda.pck --output=bur_out
```

The recovery reports 18 files and produces readable `main.gd` and `wheel.gd`. The interesting one is `main.gd`: it holds the roulette logic, the balance checks and, at the end, two things that matter:

```gdscript
const _EF: = [178, 199, 143, ...]  # 151 bytes
func _decode_flag() -> String:
```

`strings` on the raw `.gdc` gives nothing useful, because compiled GDScript is a `GDSC` container with compressed content. Decompilation is the only practical route, and the engine version in the header tells the tool which bytecode dialect to use.

Why this step: the flag is not stored in clear text, but the routine that produces it is shipped to every player.

## 3. The decoding routine

The script keeps an LCG with four constants and reconstructs the text byte by byte:

```gdscript
const _SA: = 3405691582   # 0xCAFEBABE
const _SB: = 322376503    # 0x13371337
const _LC: = 1664525
const _LA: = 1013904223

var state: int = (_SA ^ _SB) & 4294967295
for i in range(_EF.size()):
    state = (_LC * state + _LA) & 4294967295
    var kb: int = (state >> 13) & 255
    out[i] = ((_EF[i] ^ kb) - i) & 255
return out.get_string_from_utf8()
```

Three details matter when porting it:

- the state is 32-bit, so every step needs `& 0xffffffff`; Python integers grow instead of wrapping;
- the key byte is bits 13 to 20 of the state, not the low byte;
- the index is subtracted after the XOR, which is easy to lose when reading quickly.

With those preserved, the bytes decode to a UTF-8 string. The result is 151 bytes long, starts with `ctfcup{` and is a joke phrase about deploying again and again.

Why this step: the algorithm is self-contained, so the flag can be computed without touching the game or the server.

## 4. Verification

Two checks were done:

1. The package served by the live instance is byte-identical to the one analysed locally (`sha256 255728bebc0358f7aebe6038f25b646050316200043a2d2b34c5313cab5048e0`, `cmp` reports no difference).
2. The decoder in `scripts/decode_flag.py` reproduces the string from `_EF` and the four constants, and the output matches the flag recorded in the original writeup character for character.

```bash
python3 scripts/decode_flag.py
```

The flag is masked here as `ctfcup{...}`.

Why this step: a hash match proves the instance ships the same data, and an independent decode proves the routine is understood, not copied.

## 5. Why the roulette does not matter

Reaching the target balance would require beating a wheel with a fixed spin sequence and an encrypted balance. None of that touches the flag: `_show_flag()` only makes the panel visible, and the text comes from `_decode_flag()`, which is local.

The same reasoning applies to the encrypted balance. `_key` is generated at startup and never leaves the process, and both `_balance_enc` and `_target_enc` are XOR-masked with it, so patching the numbers in the browser memory is enough to unlock the panel, but the panel is not the secret.

Why this step: recognising that the UI is theatre keeps the work on the client package.

## 6. Dead ends

- Trying to win the roulette honestly: the sequence is fixed and the balance is masked, but the flag does not depend on it.
- `strings` on `main.gdc`: compiled bytecode, nothing readable.
- Looking for an API endpoint that returns the flag: the instance serves static files only.
- Fighting the `F12` handler inside the page: unnecessary, the browser menu and a fresh profile both work.

## 7. Lessons

- Anything a Godot HTML5 export refuses to give up is still shipped to the client; the package is the source of truth.
- Obscured constants are not secrets: an LCG that lives in the same file as its ciphertext is a decoder, not a lock.
- Check the container format and engine version first, then decompile; guessing byte offsets wastes more time than installing the right tool.

One paragraph: the instance is a Godot 4.4.1 web export, so the whole game including its compiled scripts is delivered to the browser; after downloading `Burmalda.pck` and recovering `main.gd` with GDRE Tools, the local `_decode_flag` routine turns the 151-byte `_EF` array into the flag with a 32-bit LCG whose key byte is taken from bits 13 to 20, and the routine is reproducible without ever playing the roulette.
