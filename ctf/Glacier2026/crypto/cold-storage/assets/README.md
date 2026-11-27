# Original organizer materials

The unmodified `cold-storage.tar.gz` is present inside the participant's private evidence archive, SHA-256:

```text
1a84fcf0c6985410954afc9a75f439042797f092af535ec421712e267c440940
```

Its 14,350-byte gzip-compressed tar contains:

- `cold-storage/src/server.js`: Express handlers `/archive`, `/api/import`, `/api/open`, and the restricted flag condition.
- `cold-storage/src/record-crypto.js`: record formatting, Temporal-based nonce, AES-128-GCM encryption and authentication.
- `cold-storage/src/archive.js`: deterministic public sample metadata.
- `cold-storage/src/public/index.html`: the browser UI with an unselectable restricted / Ice-VII option.
- `cold-storage/entrypoint.sh`, `Dockerfile`, `package.json`, `package-lock.json`, deployment scripts and organizer `sha256sum`.
- `cold-storage/flag.txt`: **dummy** flag, not the remotely retrieved one.

The agent report records `sha256sum -c sha256sum` passing for the 11 organizer files. The raw organizer archive and extracted tree are kept in the private bundle, not duplicated in this public text-only write-up.
