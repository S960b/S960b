# P31 integration — ChatGPT-owned branch

This directory is a **staging integration**, not a replacement for P30.

## Current blocker

P30's `native_gateway.py`, the Git authority CLI and their tests are not in
the GitHub main tree. Do **not** replace the installed P30 gateway with the older
`bridge_server.py`. Before integration, copy the sanitized P30 source into
the repository for review (without any credentials, token stores, runtime
databases or log dumps). The assistant writes/merges the actual patch; MT is
only to execute safe, explicit checks/deployment instructions.

## Implemented in this branch

- `p31_contract.py`: per-tool MCP hint dictionaries and safe structured
  error redaction. Critical: preserve known authority error codes instead of
  turning all failures into `authority_unavailable`.
- `test_p31_contract.py`: self-contained unittest coverage.

## Integration checklist

1. Apply annotations at FastMCP tool registration (SDK version may require
   `ToolAnnotations(...)` rather than plain dict).
2. Ensure `task_id` is required for *both* `dialog_view` and `dialog_append`
   in the published schema, then refresh the client tool catalog.
3. Map authority protocol's existing error envelope via
   `error_from_authority`; no exception messages or tracebacks in responses.
4. Add `last_message_at`, computed `seconds_since_message` and bounded,
   configurable stall detection without violating the turn-owner invariant.
   Recovery must be explicit, authorized, audited and idempotent.
5. Verify append event notification for every new dialog, with dedupe and retry.
6. Add bounded read-only health response; document 256 KiB view cap.

**Do not advertise `dialog_create` as idempotent until the implementation
guarantees repeat-safe create semantics with an idempotency key.**

## Local checks

```sh
cd research/hermes_chat_bridge/p31
python3 -m unittest -v test_p31_contract.py
```

No release/deployment is authorized solely by merging this staging branch.
