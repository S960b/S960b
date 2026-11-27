# LosVault — evidence matrix

The participant's `glacierctf_losvault_materials.zip` preserves `REPORT.md`, `MANIFEST.txt`, original prior-session write-up, internal certificate, three PCAPs, three evidence JSONs, proof, factorization verification and exploratory Python scripts.

| Claim | Evidence | Verification status |
| --- | --- | --- |
| Key generator chooses `q≈p+2^240` | `assets/suspicious_note.md` | **Directly inspectable source** |
| Internal TLS uses RSA-1024 | `assets/internal_cert.pem` | **Reverified by independent local factorization** |
| `N=pq` factors immediately | `scripts/losvault_fermat.py`, `evidence/verification_fermat.txt` | **Factors in 1 Fermat iteration; OpenSSL RSA key ok** |
| Three different bot TLS captures exist | `assets/tls_1285.pcap` through `tls_1287.pcap` | **Distinct hashes, each 2211 bytes** |
| Captures advertise `TLS_RSA_WITH_AES_128_GCM_SHA256` | `evidence/evidence_1285.json` through `evidence_1287.json` | **Recorded metadata**, not re-decrypted in packaging |
| Recovery response contains a reusable integrity proof | `evidence/proof.txt`, `evidence/previous_session_writeup.md` | **Historical prior-session report**; raw decrypted HTTP not archived |
| Final `POST /vault/evidence/<id>/verify` returns flag | `evidence/previous_session_writeup.md`, `FLAG.txt` | **Historical prior-session report**; final HTTP response not archived |
| CTF accepted recovered flag | Participant's official accepted-challenge list | **Participant confirmed** |

## Useful original scripts

- `scripts/losvault_fermat.py`: original final RSA factoring helper; also in this GitHub repository.
- `scripts/los_getpcap.py`: original session-cookie-based evidence downloader; session jar path points to a **private local file** not retained in the bundle.
- `scripts/los_capture_note.py`, `losvault_xss_test.py`, `losvault_xss2.py`: exploratory bot/JavaScript payload tests, not confirmed final trigger.
- `scripts/los_ssrf_test.py`, `los_export_test.py`, `losvault_render_test.py`, `losvault_cb_test.py`, `los_delbatch.py`: failed or supporting tests. Some use a historical personal callback address; these have not been copied into public GitHub.

## Missing artifacts

- Final verification request and raw response; TLS decrypted plaintext was not saved as a separate file.
- A live organizer instance and original review-bot trigger POST payload.
- Prior session's `lvjar.txt` session cookie file and regenerated private key. Neither should be published.

## Reproduction (using authorized local challenge files)

```sh
python3 scripts/losvault_fermat.py internal_cert.pem --out vault_internal.key
openssl rsa -in vault_internal.key -check -noout
tshark -r tls_1287.pcap \
  -o "tls.keys_list:127.0.0.1,9446,http,./vault_internal.key" \
  -d tcp.port==9446,http \
  -T fields -e http.request.method -e http.request.uri \
  -e http.response.code -e http.file_data
```

**Do not mistake the public 2048-bit reverse-proxy HTTPS certificate for the vulnerable 1024-bit internal service key.**
