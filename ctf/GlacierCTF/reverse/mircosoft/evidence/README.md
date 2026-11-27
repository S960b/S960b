# mircosoft — evidence and limits

## Confirmed from the original uploaded files in the current session

- `mircosoft (1).tar.gz` contains exactly the organizer executable, license and administrator memory dump, plus checksum file.
- All **three** organizer SHA-256 entries passed independent checks.
- PE is **PE32+ x86-64**; embedded license data is not a plaintext flag in the handout.
- MiniDump contains **five streams**, including a **seven-region MemoryListStream**.
- Regions 0–6 were extracted independently and the exact 48+4+36+16+6 = **110 fingerprint bytes** were recovered at the offsets used by the historical decryption script.
- Fingerprint SHA-256: `9887f856a75c76842491cd7fff0ee47410c3dc97cebeb71c8cc6db624b24c238`.
- Raw `license.bin[8:24]` expected MAC: `8305d5d5c5316f76bd27ddbbc5221f56`.

## Confirmed by previous November 20, 2026 solve session

- The loader's protected PE was unpacked and its license functions reverse-engineered.
- The four 16-byte embedded keys and hardware identifiers were used to compute a MAC **matching** the stored header.
- The 16-byte state-to-key transform and eight-block TEA-like CTR keystream decrypted the license payload to the accepted flag.
- Original end-stage `solve.py` was preserved and is included verbatim in the public repo; it prints the fingerprint fields, MAC comparison, derived key and decrypted result.

## Absent intermediate products: do not invent them

- Original PE unpacking helper and exact unpacking algorithm/trace from the older working directory.
- The separately unpacked `unpacked_pe.bin` itself; **it is not in the organizer archive**.
- Unpacked PE's four secret keys and recorded 16-byte intermediate states.

Because of these missing items, running `scripts/solve.py` against *only* the handout fails until an unpacked PE is provided. The new `scripts/extract_minidump.py` fully closes the MiniDump preparation gap but **does not claim to decrypt the PE loader**.

## How to verify the available stage

```sh
tar -xzf 'mircosoft (1).tar.gz'
python3 scripts/extract_minidump.py mircosoft/admin_machine.dmp --out-dir ./work
```

Validate the program's listed device IDs against the five spans; the resulting fingerprint SHA-256 is the independent check above.

The actual flag is already known from the participant's accepted solve. It remains masked in public write-ups under the repository's existing redaction convention.
