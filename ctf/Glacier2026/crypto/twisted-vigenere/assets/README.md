# Twisted Vigenere — organizer handout provenance

Official `twisted-vigenere (1).tar.gz` uploaded for this write-up:

```text
SHA256 a773b23f6b9900ff0769c3bf4b3d0f189958a0bac88d2c87f2b44ebf3099a898
```

The original tarball contains exactly:

| Organizer file | Bytes | SHA-256 |
| --- | ---: | --- |
| `gen.py` | 1,025 | `f4fbecb6f174eebc5cd67f20c841855bbbe4e2ffb69a4d6a9634d0f17b2db10b` |
| `output.txt` | 3,427 | `26560d98497eb305c741f33779bb7724edaa2301b3a8c80c7e6ada377a57bf77` |
| `sha256sum` | 313 | `c13cb432a3d25e9327601db2af77d49248812ca12ac6fa9d88b541128daab455` |
| `pyproject.toml` | 120 | Verified against organizer sha256sum |
| `uv.lock` | 21,076 | Verified against organizer sha256sum |

The extracted original file `gen.py` is [preserved](gen.py) in this GitHub repository without modifying its code. Its `numpy` dependency is only used to implement a repeating-key XOR. The new recovering script `scripts/solve_mt.py` (one directory above) requires no third-party dependencies.

**Important:** the output.txt must be the one from the original archive. Rerunning `gen.py` without `flag.txt` uses the demo flag and fresh MT19937 randomness, producing a different ciphertext; it is not a reproduction of the competition instance.

The exact original ciphertext is retained in the supplied tarball, and the full 48-byte accepted flag is deliberately omitted from public Markdown. The mathematical extraction is fully reproducible with the original ciphertext and the public solver.
