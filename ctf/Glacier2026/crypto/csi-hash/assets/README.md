# CSI-HASH organizer handout

Original `csi-hash.tar.gz` (4,274 bytes), SHA-256 `90140513b2577f1d1aee63851bb541761d83487003c518171fc6c5cdd120c511`. The user-supplied private evidence ZIP includes the **unaltered** organizer tarball and extracted `assets/handout/` files:

- `challenge.sage`: Sage implementation of the isogeny hash and 32-letter challenge.
- `Dockerfile`: base `sagemath/sagemath` Sage 10.6; `socat` and a 180-second timeout.
- `docker-compose.yml`: port 1337 and `TIMEOUT=180`.
- `entrypoint.sh`, `deploy.sh`, `deploy.bat` and `sha256sum`.
- `flag.txt`: **placeholder**, not the live flag.

The original checksum file was verified successfully: `sha256sum -c sha256sum` reported seven files OK. The challenge uses `GF(p²)`, Montgomery elliptic curves and prime-degree isogenies; it does not rely on a conventional digest primitive.

The original challenge source includes two additional defects which the exploit avoids by submitting exactly 32 bytes: a length check against 4 instead of 32, and a missing loop index increment in the multi-block loop.
