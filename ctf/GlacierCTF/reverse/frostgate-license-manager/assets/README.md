# Original organizer archive

Uploaded handout: `virt (1).tar.gz` / organizer file `virt.tar.gz`, original challenge URL on the GlacierCTF platform.

```text
virt/virt.exe       154624 bytes
virt/sha256sum      77 bytes
```

The organizer's `sha256sum -c sha256sum` passes for `virt.exe`.

The executable is **PE32+ / x86-64 Windows console**, with base address `0x100000000`, entry point RVA `0x16a0`, and these sections:

| Section | Virtual size | File offset |
| --- | ---: | ---: |
| .text | 0x175b0 | 0x400 |
| .data | 0x1524 | 0x17a00 |
| .rdata | 0x69ac | 0x19000 |
| .pdata | 0x16d4 | 0x1fa00 |
| .bss | 0x2448 | no raw data |
| .CRT | 0x18 | 0x21200 |
| .idata | 0xd09 | 0x21400 |
| .frost0 | 0x38e4 | 0x22200 |

The custom `.frost0` segment is compatible with the challenge's virtualization/obfuscation premise; the section name alone does **not** establish individual VM opcodes.

The archive carries no source, pseudocode, accepted key, debug symbols or pre-made reversing scripts. Its executable and checksum file remain in the user-provided attachment rather than being redistributed in the public GitHub tree.
