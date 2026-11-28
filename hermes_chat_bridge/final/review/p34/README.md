# P34 — retirement of legacy MCP methods

**Deployed 2026-11-04** to the separate bridge source:
`/home/kali/S960b/research/hermes_chat_bridge/bridge_server.py`.
This file belongs to a **different** repository (`S960b/S960b`), and is not maintained as a copy in this project.

The live sha256 reported by MT after rollout:
`d0129dc19ac241d8272abe7ea36f6e2bfa2b1ec33d348bb43ded68ee2cf662b5`.
The original sha256 before P34:
`48f3a710904269b1f0f335a98c0e151e509fb0c39d661a2fdf2e0aef93e846e9`.
The local backup is `/home/kali/.local/backups/p34-pre-20261104_143032`.

**Verified private Git backup (4 November 2026):** [`backup/p34-live-source-20261104`](https://github.com/S960b/agent-dialogs/tree/backup/p34-live-source-20261104), commit `825fad1cacb04188d337615cc47f373cebb924b5`, file `review/p34/snapshots/bridge_server_p34_live_20261104.py`. MT re-read the published blob and confirmed its SHA256 is identical to the deployed source above. This snapshot is **private**; the public `S960b/S960b` working tree remains intentionally unchanged and still contains unrelated uncommitted files.

`retire_legacy_mcp.py` is the original ChatGPT-authored, SHA-pinned staging script.
`check_staged_retirement.py` is the verified **v2** offline harness copied from the original v2 blob; the old incompatible SDK harness is intentionally not retained in this canonical release folder. The test expects MCP SDK 2.3.0 available in the isolated test environment.

Post-deployment MCP tools: `dialog_create`, `dialog_creation_status`, `dialog_list`, `dialog_view`, `dialog_health`, `dialog_append`. Old `bridge_*` tools and legacy MCP events handlers are unregistered. Historical internal functions may still be present, but this does not make them part of the public MCP interface.

This is an **audit record and recovery source**, not an instruction to rerun deployment on a running bridge. Verify the current live SHA, dependencies, backups and service status before any future release.
