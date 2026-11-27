# LosVault — recovered artifacts

There was **no organizer handout archive** preserved for this web-only challenge. The user's `glacierctf_losvault_materials.zip` is an *investigation evidence bundle*, gathered from a **parallel earlier solve session**, not a file published by the organizers.

Relevant assets in the private bundle:

| Private file | Description | SHA-256 |
| --- | --- | --- |
| `assets/suspicious_note.md` | leaked internal RSA generator source | `12cc37fb296677bfa6fea978b77ed0c494457c329ba4c77c22bc215d433be45b` |
| `assets/internal_cert.pem` | internal TLS public X.509, CN=localhost | `055ea7fd5aafd59d4eb265e4e7b0e8b11205d50b39eacdf43d8eb62edaa02f4c` |
| `assets/internal_cert.der` | same public X.509, DER | `54a240b8aa000ea8a4a1e6b2e06c9cb3c31b8bbd003550d2a35ac43eca2410b7` |
| `assets/tls_1285.pcap` | encrypted review bot session, 2211 bytes | `66b6211954b9ab05bb108ad8ef1e9ce213cd5fc69736e1f190cb255a8ccdfc18` |
| `assets/tls_1286.pcap` | second encrypted session, 2211 bytes | `9bfbcb530cf754f06fd7aab42801eaac8d61f07fbe4b66bea754f58b95d2edee` |
| `assets/tls_1287.pcap` | third encrypted session, 2211 bytes | `84ede970ac3a1c15d004c30d70ba12209deddb64bc57ccc5c7a52c0e3072c344` |

The captured `assets/suspicious_note.md` was obtained **on the application**, not included with an official download. It provides `_generate_rsa_key` and the decisive line:

```python
candidate = (p + (1 << 240)) | 1
```

The X.509 is a **public key certificate**. The corresponding private key was recovered using `losvault_fermat.py` but was intentionally not included in the evidence bundle: it is straightforward to regenerate from the certificate.

The PCAP files, evidence JSON and proof are retained in the user's original ZIP and not added to this public repository: the files may contain internal service headers, session-specific identifiers or verification material. The documentation provides commands to inspect a local copy of the authorized CTF evidence.
