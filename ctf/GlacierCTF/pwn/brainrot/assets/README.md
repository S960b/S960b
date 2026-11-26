# Original organizer handout

These original files are preserved in the participant's private `glacierctf_brainrot_materials.zip` under `assets/`. Large binary artifacts are not republished.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `bf.c` | 2423 | `a037d111c0100c83d854ed24fffe80d20851409b3f051351ba8bd6f9402c8d87` |
| `bf.sweb` | 84128 | `0c76c4ed356d48d4d6fe50747cafe64bdd23f2e404a695fc1aa840bbc5f1620a` |
| `brainrot.vmdk.gz` | 491046 | `e1892f8e654839a471eb24805adce3cf7d844791e3c516ec5940f76cc08dad3a` |
| `exe2minixfs` | 993064 | `c8a512dd4a2a76f5fbe0150bbbda06b21474711326ce22c999040e6719e1c967` |
| `run.sh` | 495 | `41c0d324cac9ce674ed95db1d41f778f4c692377079a17135fa490876799d178` |

The original `brainrot.vmdk` expands to 134,217,728 bytes, and was intentionally omitted from the bundle. The original `bf.c` has the vulnerable `printf(outbuf)` inside `flush_out()`; the published explanation quotes the relevant source exactly.

The handout includes the build/run infrastructure necessary for a local isolated SWEB reproduction, not a normal Linux ELF exploit environment.
