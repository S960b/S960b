# Stage35F permission repair / resume

Stage35F first install succeeded but was stopped at preflight due to mode `0644` of SHA-pinned Stage35D/Stage35C source files. No diagnostic was attempted, so this is *not* an `once` replay.

This package does not install or modify Stage35F sources. It verifies the already-installed Stage35F against its original SHA256SUMS, refuses if its one-shot attempt marker or log exists, and changes permissions of **only**:

- `/home/kali/hermes-pg-stage35d-local-smoke/stage35d.py` (exact SHA-256 pinned)
- `/home/kali/hermes-pg-stage35c-static-guard/static_guard35c.py` (exact SHA-256 pinned)

Preconditions: both files regular, owned by `kali`, single-link, correct hash, and mode exactly `0644` or `0600`; parent directories owned by `kali` and not group/other writable. Uses O_NOFOLLOW and inode-bound `fchmod`, setting mode `0600` only. All files are verified before any chmod. Does not chmod directories, secret files, executables, units, config, DB or tunnel. Has no automatic retries.

Run exactly once as `kali` via the pinned ZIP command provided by ChatGPT. The run validates source manifests, invokes installed Stage35F `preflight`, then invokes existing `stage35f.py once`. Afterwards it invokes the already-installed allowlisted private-log audit. A failed diagnosis after creation of Stage35F attempt marker MUST NOT be retried.

Expected success: `STAGE35F_RESUME_DIAGNOSTIC_SUCCESS_NO_TUNNEL_CUTOVER`. Other `STAGE35F_*` markers describe the diagnostic result, read-only database inspection and shutdown behavior.
