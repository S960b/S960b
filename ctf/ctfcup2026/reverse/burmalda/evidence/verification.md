# Verification log

Instance checked on 2 November 2026.

## Package

```text
GET /Burmalda.pck -> HTTP 200, 43760 bytes
sha256 255728bebc0358f7aebe6038f25b646050316200043a2d2b34c5313cab5048e0
```

The file served by the instance is byte-identical to `assets/Burmalda.pck`
(`cmp` reports no difference).

## Recovery

```text
gdre_tools --headless --recover=Burmalda.pck --output=bur_out
Use Godot editor version 4.4.1 to edit the project.
Recovery finished in 00m00s
```

Produced `main.gd` (source of the `_decode_flag` routine) and `wheel.gd`.

## Decoding

`python3 scripts/decode_flag.py` returns one UTF-8 string that starts with
`ctfcup{` and ends with `}`, 151 payload bytes in total. The value is masked
here as `ctfcup{...}`; it matches the flag recorded in the original writeup
character for character.
