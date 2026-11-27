# Organizer handout: Cold Echo

The uploaded private `glacierctf_cold-echo_materials.zip` contains the **unaltered** 2,271-byte organizer archive `assets/cold-echo.tar.gz` and its three extracted files:

- `assets/README.md` (1,648 bytes): official math, canonical receipt and flag specification.
- `assets/archive.json` (2,555 bytes): exact numerical trace, forward observation, P-256 public key, DER signature and flag mask.
- `assets/sha256sum`: organizer's hashes for README and archive.json.

Organizer archive SHA-256:

```text
a0776d87b60944a1e6d0919012dc4a759212f2d08c015c46d6036f34bc650877
```

Organizer data:
- `prime=17179869143`; `degree=42`; `batch=bed64d3525599836`
- `trace`: 42 readings `P(u)*P(u^-1)` over the field
- `forward=[89,11033196826]`
- `public_key_pem` (ECDSA P-256)
- `receipt_der_b64` (ECDSA/SHA-256, ASN.1 DER)
- `flag_mask_b64` (masked flag body)

The signed receipt begins `GLACIER-COLD-ECHO-v1\n`, includes batch, prime/degree, every trace pair in its source order, forward measurement, and 43 decimal coefficients in ascending degree order, each line ending with LF.

The public GitHub repository contains the complete derivation and original scripts; the numerical `archive.json` and intermediate evidence remain inside the user-supplied ZIP, with the real flag masked in public docs.
