# Hermes PG v2 — Stage 34F-A: one disabled managed pilot

## Scope

Creates exactly **one** managed dialog under the real PostgreSQL `bridge_chatgpt` login, with fixed `request_id=stage34f_pilot_20261123_v1`. Registry `dispatch_enabled=false`, `policy_version=0`, dialog `last_seq=0`, `version=0`. No messages, SDK, claims, acknowledgements, plugin configuration changes, Secure Tunnel switching or write-gate creation.

The prior Stage 34E-B candidate must exist with SHA256 `b3679086583d77c96b6ed1c3dc7b1ed1656707dabba4d3ac74843fe64177d730` and remain unregistered. The live canary remains at seq6, and the current MT config retains exactly 7 plugins. The SSH systemd shadow forward on 127.0.0.1:15433 is used through existing pin-verified Stage33C actor credentials.

## Safety

- A private `.create-attempted.json` is fsynced **before** the only write. Re-running is deliberately refused, even if the transaction result is ambiguous.
- `88_verify_only.sh` reconciles the one stable `request_id` against the real database, without creating another dialog. It works even when local receipt files were not written because the connection failed just after commit.
- The two side files `pilot-created.json` and `pilot-binding-draft.json` are private 0600. The latter is **data only**, never an installed plugin config.
- `bridge_chatgpt` is the authenticated DB actor. These operations are run locally by the user-authorized agent at ChatGPT's direction; the current Tunnel MCP still exposes only bounded canary tools.
- If the registry is not empty or canary status differs, creation refuses with no database change.
- If a fault occurs after the attempted marker, do not run the create script again, even if a read-only query reports the pilot absent. Ask ChatGPT for a recovery plan.

## Operator commands

Extract ZIP once under `/home/kali` (it contains a top-level directory). After verifying outer archive SHA256 and `SHA256SUMS`, run `python3 -m unittest -v test_stage34f` and `bash -n` on all shell files.

```
bash 86_readonly_preflight.sh
bash 87_create_pilot_once.sh
bash 88_verify_only.sh
```

Expected: `STAGE34F_ONE_MANAGED_PILOT_COMMITTED_DISABLED`, `STAGE34F_PILOT_TASK_ID=task_<32 hex>`, `STAGE34F_ONE_DISABLED_MANAGED_DIALOG_VERIFIED`, `STAGE34F_NO_TASK_NO_CLAIM_NO_ACK_NO_SDK`, `STAGE34F_CANARY_AND_LIVE_CONFIG_UNCHANGED`.

## Next stage — not part of this package

A second guarded package will use the recorded `task_id` to install the unregistered pilot-only receiver through an exclusive MultiTool process handoff. Dispatch policy stays disabled until the new receiver is proven loaded with exactly that task ID and no other consumers. Then a bounded, authenticated ChatGPT MCP may send one harmless pilot task and inspect/acknowledge the result. Never activate managed dispatch or issue a task before those gates.
