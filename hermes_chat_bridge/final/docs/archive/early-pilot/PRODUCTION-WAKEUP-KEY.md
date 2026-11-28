# Dedicated beta wakeup key

Latest runner changes the earlier interactive test-key flow. Only an explicit
`key-init` operation creates a **new** 32-byte random key in the separate
candidate state. No real key was provisioned by development/offline tests.

```
PYTHONPATH=src python -m git_dialog.runner key-init --state-dir NEW_CANDIDATE_STATE
```

This requires an existing candidate marker and valid owner database, the same
UID/700 state directory and exclusive server flock. It does not start a server,
subscribe, forward an event or modify owner identity. Existing P1 state without
the candidate marker is rejected. No existing key is rotated/overwritten.

The fixed `wakeup.key` is binary, UID-owned, regular, single-link and 600.
Directory and file handles use NOFOLLOW; provisioning uses O_EXCL, complete
write and fsync(file + directory). Loading uses NONBLOCK/NOFOLLOW and exact
32-byte size. Missing, symlink, hardlink or incorrectly permissioned files fail
closed without chmod/repair. Partial files after an I/O failure remain rejected;
do not blindly retry or delete them. Local same-UID process compromise is not
prevented by filesystem permissions.

`serve --enable-wakeup` loads only this fixed dedicated file. It no longer
prompts for arbitrary test keys; there is no key/secret CLI argument or env
loader. Default-off serve does not read it; serve never generates a key.
No key bytes, path or fingerprint appear in success/error JSON. The forwarding
adapter must use this same dedicated key through explicit local configuration,
not reuse a PAT, owner password, subscription secret or public fixture key.
Do not print, copy into chat/Git/bridge/logs, or include key bytes in argv/env.

This is provisioning **code**, not a claim that real provisioning/forwarding
has occurred. The existing bridge still needs its reviewed opt-in event hook;
the contract diagnostic is separate and must not be bypassed. HMAC wakeup grants
only a coalesced refresh signal, not owner read access or Git write authority.
No writer gateway or real close/delete is enabled by this stage.
