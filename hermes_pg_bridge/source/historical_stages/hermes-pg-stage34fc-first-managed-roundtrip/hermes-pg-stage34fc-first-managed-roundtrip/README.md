# Stage 34F-C — first real managed PostgreSQL pilot roundtrip

This package is authored for the exact Stage 34F-B committed setup, **8 plugins**,
`opencode.json` SHA256 `4b32ac0fe83c902f5a7e2e5a749b104506987e1380aadc863ff15ca57b7786c3`.

Pilot: `task_bddaadef89b7458fb7f8cf437dc0f023`, registered and disabled,
`policy_version=0`, `version=0`, `last_seq=0`, no messages. Existing canary:
`task_f2c64cfdf7894537b35a19a6def977c6`, seq5 and seq6 already delivered.

## Exact one-time behavior

`96_activate_and_send_once.sh`:

1. Requires `kali`, correct pinned live eight-plugin configuration, pinned managed
   CLI and JS, correct actor identity, precisely one disabled managed pilot, no
   existing task, no managed `inflight`, healthy idle plugin tick, and all canary
   receipts unchanged.
2. Writes `.activation-attempted.json` with `O_EXCL` and fsync **before** writes.
3. Creates the exact CLI write authorization file (`0600`, `O_EXCL`), no
   overwrite. This allows the managed CLI to claim/ACK/report; the JS pilot is
   already registered in MultiTool with one allowed task ID.
4. Uses a **single PostgreSQL transaction under login `bridge_chatgpt`**:
   `cg_set_managed_dispatch(pilot,true,0,0)`, then
   `cg_send_managed_task(pilot,"pgv2-mg-34fc-pilot-task-20261123-v1", BODY, 0)`.
   Before COMMIT it verifies enabled policy version 1 and dialog version 1,
   `last_seq=1`. If any error occurs before COMMIT, neither the policy nor the
   message becomes committed. The authorization file and attempt journal may
   still remain and must be reviewed; **never rerun**.
5. The registered plugin (and not this Python script) is responsible for calling
   the actual MultiTool SDK on a later poll, writing the report via its pinned
   managed agent CLI, and reconciling the task ACK. No Git, changes to the MCP
   tunnel, plugin registration, or SQL DDL in this stage.

Report expected **exactly** (single line, no newline):

`PGV2_MANAGED_PILOT_ROUNDTRIP_OK_20261123`

The report message_id must equal the deterministic JS ID
`pgv2-mg-bddaadef89b7458fb7f8cf437dc0f023-1-report`.

## Actions, all only under user `kali`

After extracting into `/home/kali/hermes-pg-stage34fc-first-managed-roundtrip`:

```bash
sha256sum -c SHA256SUMS
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python -m unittest -v test_stage34fc
bash 95_readonly_preflight.sh
bash 96_activate_and_send_once.sh  # EXACTLY ONCE
bash 97_verify_only.sh             # read-only; safe to repeat
```

After agent SDK has produced the exact report and the task delivery is
`delivered` with no `inflight` reservation, and after inspecting its read-only
status, the **separate** authorized step is:

```bash
bash 98_ack_report_once.sh          # EXACTLY ONCE, only after verified report
bash 97_verify_only.sh
```

This finalizes the ChatGPT-directed seq2 receipt as `delivered`. No further
messages are sent. Leave general write permissions and the policy unchanged
until a dedicated shutdown/revocation step.

## Failure / ambiguity

- Do **not** repeat `96_activate_and_send_once.sh`, even if there is no success
  marker. The journal is written before the first database write and PostgreSQL
  may have committed before a connection failure.
- Do **not** repeat `98_ack_report_once.sh` if the result is unclear.
- Only `97_verify_only.sh` is safe to repeat. It checks the unique known
  message ID and canary state without writing or exposing secret tokens.
- If the report is incorrect, the report receipt or SDK outcome is uncertain,
  or task delivery remains `leased`, stop. **Do not start a second SDK call**.
- Never copy a lease token, PostgreSQL password, SSH host/IP, DSN, or contents
  of other dialogs into a report.
- `94_verify_after_multitool_launch.sh` from Stage 34F-B checks a *disabled*
  pilot and is intentionally **not** used after this activation.

This is a bounded first-run experiment, not a general Secure MCP endpoint.
The presently connected ChatGPT MCP is still limited to the old fixed canary.
The executor runs this exact ChatGPT-authored script under separate PostgreSQL
`bridge_chatgpt` credentials; it does not design the code.
