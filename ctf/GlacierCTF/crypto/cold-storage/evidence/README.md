# Cold Storage — evidence and provenance

Original evidence: two byte-identical user-supplied archives `glacierctf_cold-storage_materials.zip` and `glacierctf_cold-storage_materials(1).zip`.

Key preserved files:

- `REPORT.md` — detailed investigation chronology, including ruled-out derivations and repeated-nonce experiments.
- `FLAG.txt` — real accepted flag (not republished here).
- `evidence/03_successful_run.log` — **historical** successful remote transcript: 32 records, one leap-second alias import, GHASH recovery, verified H, and `FORGED OPEN: 200` returning a real URL-encoded flag.
- `evidence/04_alias_probe.log` — `:60` **does not** equal the next minute's `:00`; HTTP 403 on the archive timestamp.
- `evidence/05_alias_bug.log` — malformed `:560Z` import rejected 400.
- `evidence/02_archive_instance1.txt` — public metadata and ciphertexts; `evidence/instances/s1.json` … `s10.json` — ten fresh instances with stable metadata and changing ciphertexts.
- `scripts/solve.py`, `scripts/exploit.py`, `scripts/session.py` — original successful Python files in the agent bundle.
- `assets/cold-storage.tar.gz` and extracted organizer source.

The archive includes the actual live `ciphertext`, `tag` and matching record from the same instance; public [test_offline.py](../scripts/test_offline.py) rechecks the field arithmetic without connecting to the service.

**Flag accepted by the CTF platform**, as confirmed by the participant for the completed GlacierCTF tasks. The original raw remote response already contains the flag. No screenshot of submission acceptance is needed to establish the status stated by the participant.

Security-relevant distinction: `H` is the GHASH authentication subkey for one random AES key, **not** the 16-byte AES encryption key, and not shared between instances.

No live instance was contacted or recreated during preparation of this write-up.
