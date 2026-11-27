# Evidence and limitations

## Accepted result from prior conversation

The previous GlacierCTF solve discussion on **November 20, 2026** records a unique valid 40-character license of the form `gctf{...}`, with **exactly one matching printable character at each position**. It also records the distinct misleading string `wrong{f1n4l1z3r_d3c0y_n0t_th3_r34l_0n3}`. The participant's later solved-challenge list explicitly includes Frostgate License Manager, so platform acceptance is confirmed by the participant.

The full accepted license was recovered again from that earlier conversation before writing this document, but is masked from the public GitHub repo according to the established publication convention.

## Verified again against the supplied original binary

- Archive contents: 154,624-byte `virt.exe`, 77-byte checksum file.
- `sha256sum -c sha256sum`: PASS.
- PE type: Windows PE32+ x86-64.
- Entry point RVA: `0x16a0`.
- Presence of a dedicated `.frost0` section at file offset `0x22200`.
- User-facing prompt strings visible in the binary's `.rdata`.
- Plain `gctf{` and `wrong{` literals were **not found** with ordinary byte-string search, consistent with, though not proof of, an encoded/virtualized check.

## Reproduction scope

No Wine/Windows runtime, original per-byte-search script, opcode map, or prior debugger output is present in this environment. No claim is made that the license was **re-executed** against the binary in this turn. The authentic historical acceptance result and structural binary verification are distinct pieces of evidence.

The original handout remains available from the GlacierCTF link and the user-supplied attachment. A public standalone verification/inspection script is provided, but it is **not** an automatic VM exploit.
