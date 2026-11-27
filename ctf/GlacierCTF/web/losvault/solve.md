# LosVault — weak internal TLS RSA, recovered integrity proof

[Русская версия](solve.ru.md)

## 1. Scope and provenance

The participant confirms this GlacierCTF 2026 challenge was solved and the flag accepted. The complete attack chain below comes from the **original prior-session write-up** in `glacierctf_losvault_materials.zip`, cross-checked against the actual leaked code, internal X.509 certificate, three pcap files, API evidence JSON and the original Fermat factorization solver.

**Important separation:** the agent who assembled this archive did **not** recreate the online attack. It independently verified the recovered **internal RSA key** and checked surviving artifacts. The prior session documents the TLS plaintext, integrity-code recovery and final `verify` response; a raw decrypted transcript and the final POST response are not in the bundle. These are historical reports, not newly observed results.

## 2. Application and dead ends

LosVault exposes Flask login, uploads, a document-review bot, transparency reports and evidence endpoints. Access to other users' uploads is checked (404), and admin routes such as `/api/admin/transparency/export`, `/api/admin/capture-tls/<id>` and `/admin/review/<id>` require the server-side `vault-audit` role; simply choosing a matching username or manipulating client session cookies did not grant it.

The previous research also attempted XSS through notes, rendering tricks, callbacks/SSRF and review-queue manipulation. Preserved probes include `losvault_xss_test.py`, `losvault_xss2.py`, `losvault_render_test.py`, `losvault_cb_test.py`, `los_ssrf_test.py` and `los_export_test.py`. None of these scripts alone is the final exploit. In particular `los_capture_note.py` constructs several JavaScript note payloads targeting the administrative `capture-tls` endpoint: **this file is an experiment, not independently verified evidence of the ultimately successful bot trigger.**

## 3. How evidence and capture were obtained

According to the **previous session's recorded successful sequence**, the bot reviews uploaded notes/files in a FIFO queue. An uploaded note referring to an internal URL causes a bot-side HTTPS request to the internal recovery service, while a capture of that TLS connection becomes available to the owner through:

```text
GET /api/evidence/<entry_id>
```

The response contains `tls_capture.download_url`, typically `/vault/evidence/<entry_id>/tls.pcap`, and associated encrypted telemetry envelopes. The archive contains **three distinct 2,211-byte** captures for entries **1285**, **1286** and **1287**, with corresponding evidence JSON. The evidence JSONs each include `record_count=200`, TLS capture metadata and the declared cipher suite `TLS_RSA_WITH_AES_128_GCM_SHA256`.

The note triggering the final capture is not preserved as an independently proven exact HTTP request; do not assert that one of the exploratory XSS payloads is the winner.

## 4. Internal key-generation flaw

The leaked `assets/suspicious_note.md` contains the actual key-generation logic (recovered during the challenge, not part of an organizer handout). Its relevant fragment is:

```python
p = _generate_prime(512, 65537)
candidate = (p + (1 << 240)) | 1
# Starting at that candidate, search upward for the next prime q.
```

The two roughly 512-bit factors are extremely close relative to their size. The RSA modulus is `N=p*q`. Set

```text
a = ceil(sqrt(N))
b = isqrt(a² - N)
p = a-b, q = a+b   (when b² == a²-N)
```

For the preserved certificate the script finds a square **immediately**, after one loop iteration. It reconstructs `d = e^(-1) mod lcm(p-1,q-1)`, serializes the private key and verifies `p*q=N`. The saved independent packaging log reports:

```text
RSA modulus bit length: 1024, exponent: 65537
Factors found in 1 Fermat iteration(s)
RSA key ok
```

This **internal CN=localhost RSA-1024 certificate** is the correct target. The public HTTPS reverse proxy uses an unrelated 2048-bit Let's Encrypt certificate; factoring that one is a mistaken lead.

## 5. Decrypt the recorded non-PFS session

The internal service listens at `127.0.0.1:9446`, and the review bot negotiated `TLS_RSA_WITH_AES_128_GCM_SHA256`. Since this TLS 1.2 suite uses RSA key transport rather than ephemeral Diffie–Hellman, obtaining the corresponding server RSA private key permits retrospective session decryption.

The exact historical recovery commands, from the agent report, are:

```sh
# Extract internal RSA certificate from the captured TLS handshake:
tshark -r tls.pcap -Y tls.handshake.certificate \
  -T fields -e tls.handshake.certificate | tr -d ':' | xxd -r -p > internal_cert.der
openssl x509 -inform DER -in internal_cert.der -out internal_cert.pem

# Recover private key (requires Python cryptography):
python3 scripts/losvault_fermat.py internal_cert.pem --out vault_internal.key
openssl rsa -in vault_internal.key -check -noout

# Decrypt using the RSA key for TLS traffic on port 9446:
tshark -r tls.pcap \
  -o "tls.keys_list:127.0.0.1,9446,http,./vault_internal.key" \
  -d tcp.port==9446,http \
  -T fields -e http.request.method -e http.request.uri \
  -e http.response.code -e http.file_data
```

In the previous session's account, the decrypted exchange was:

```text
GET /internal/recovery?entry_id=<id>
response: proof=<integrity-code>
```

The response's integrity proof was the **same across all recovered entries**. The original public write-up redacts the concrete proof because it functions as a challenge secret; its exact value remains in the user's private `evidence/proof.txt`.

The assembly agent did not repeat `tshark` decryption when packaging the files. The certificate, captures, `proof.txt` and local Fermat verification are preserved; the decrypted HTTP body itself was not saved as a standalone evidence file.

## 6. Submit the integrity code

The evidence page has a **RECORD INTEGRITY** form, accepting `recovery_code`:

```http
POST /vault/evidence/<entry_id>/verify
Content-Type: application/x-www-form-urlencoded

recovery_code=<value-from-decrypted-recovery-response>
```

The earlier successful session reports the response **“Vault integrity confirmed”**, followed by the actual GlacierCTF flag. The accepted flag is masked in this public repository, but retained in the user's private `FLAG.txt`.

The final POST's full raw HTTP response/cookie jar and screenshots are **not** part of the provided evidence ZIP. These do not affect the participant's confirmed success, but limit our ability to replay every historical action exactly.

## 7. Why this chain works

The flaw is not merely “small RSA.” The destructive combination is:

1. a key generator that forces RSA factors near each other;
2. classical `TLS_RSA` without forward secrecy, allowing captured sessions to be decrypted later with that key;
3. a review/evidence design exposing internal encrypted traffic to users whose uploaded documents are being reviewed;
4. a sensitive integrity proof carried inside that internal traffic and then accepted by the public verification endpoint.

A correctly generated RSA-1024 key would not factor instantly by Fermat's method, although RSA-1024 itself is no longer appropriate for modern security. The correct remediation is modern TLS 1.3 / ECDHE, independent strong key generation, avoiding unnecessary internal TLS capture exposure, and not reusing recoverable bearer-style integrity codes.

## 8. Evidence and reproducibility

The private archive includes `REPORT.md`, `evidence/previous_session_writeup.md`, the leaked `assets/suspicious_note.md`, `assets/internal_cert.pem` and DER, `assets/tls_1285.pcap`, `tls_1286.pcap`, `tls_1287.pcap`, `evidence/evidence_1285.json` through `evidence_1287.json`, original agent scripts and independent `verification_fermat.txt`. Their digests were listed in the archive manifest and checked in this review.

The public [Fermat solver](scripts/losvault_fermat.py) is the historical original. [assets/README.md](assets/README.md) and [evidence/README.md](evidence/README.md) distinguish reverified steps from previous-session assertions. No real user credentials or internal verification tokens are published.
