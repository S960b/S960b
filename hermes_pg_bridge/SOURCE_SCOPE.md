# Public source export: audit scope

This is a **non-executable source snapshot** of the current local Python implementation,
installed managed Inbox plugin, prepared for independent security review.
Installed sources include one-shot stage installers that **must not be run on a live environment**.
Historical stage archives are excluded from this publication by the owner's decision.

- `AUDIT_MANIFEST.json` lists provenance and SHA256 of source and published bytes.
- Source paths are relative to the export; no runtime logs, dialogs, credentials, DSNs or database dumps were exported.
- Public/private IP literals may be replaced by REDACTED_IPv4; the SHA256 of both original and published file are retained.
- Secret indicators cause publication to STOP. The only two exception types are path-and-syntax-pinned examples (dynamic password call in stage33c and user:pass or x:y at login.example.org negative-test URL in stage35b). The audit exporter test fixture archive (Stage41) is excluded. All other findings block. Files omitted for security are identified without exposing content.
- Live systemd unit configuration is excluded; unit NAMES are included as inventory only.
- Snapshot refreshed on 2026-11-28: historical stage archives were removed from this publication; live and installed sources are current.
- Legacy `dist/mt-pg-inbox*.mjs` builds are excluded: the live runtime loads the managed receiver published as `source/runtime_plugins/plugin_1.mjs`.
- This publication is an audit aid, not a proof of running version or deployment instructions.
