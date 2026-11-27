# mircosoft — scripts and reproduction

[English write-up](../solve.md) · [Русский райтап](../solve.ru.md)

## 1. Extract MiniDump memory regions

```sh
tar -xzf 'mircosoft (1).tar.gz'
python3 scripts/extract_minidump.py mircosoft/admin_machine.dmp --out-dir ./work
```

The **new** `extract_minidump.py` uses only the standard library. It reads the MiniDump `MemoryListStream`, writes `region0.bin`–`region6.bin`, verifies file boundaries and computes the hardware fingerprint SHA-256. The expected result for the original archive is

```text
Fingerprint length: 110
Fingerprint SHA256: 9887f856a75c76842491cd7fff0ee47410c3dc97cebeb71c8cc6db624b24c238
```

## 2. Historical final decryption

`solve.py` is the **original retained solver** from the successful November 20, 2026 solve session. It expects a hardcoded `B=Path('/mnt/data/mircosoft_work')`, and accesses:

```text
/mnt/data/mircosoft_work/unpacked_pe.bin
/mnt/data/mircosoft_work/mircosoft/license.bin
/mnt/data/mircosoft_work/region0.bin
/mnt/data/mircosoft_work/region1.bin
/mnt/data/mircosoft_work/region2.bin
/mnt/data/mircosoft_work/region3.bin
/mnt/data/mircosoft_work/region4.bin
```

To use an arbitrary working directory, change the `B` assignment at the top of `solve.py`. The previous solve generated `unpacked_pe.bin` by unpacking the protected launcher, but that intermediate and the unpacking helper were **not provided in the organizer's tarball**, so the currently published two scripts **are not an independent end-to-end solver starting from only the supplied archive**.

The stages implemented by `solve.py` are:

```text
unpacked PE .data keys → fingerprint MAC matches license header
→ three successive MAC groups → 16-byte derived key
→ 8 × custom TEA(counter, key) → XOR with license ciphertext
→ plaintext/accepted flag
```

Historical MAC match and flag acceptance are documented in the write-up; this publication independently re-extracted the MiniDump but did not rerun the missing PE-unpacking stage.

The script keeps its historical algorithm unchanged for auditability.
