# Hermes PostgreSQL Bridge — security audit source

[Русский](README.ru.md) · [Previous Git-based bridge](../hermes_chat_bridge/)

**Status (2026-11-28): sanitized audit source snapshot published.** [Source scope](SOURCE_SCOPE.md) · [Manifest with provenance and exclusions](AUDIT_MANIFEST.json). This is not a private runtime backup.

[Independent audit checklist](AUDIT_REQUEST.md)

## What this project does

A PostgreSQL-backed managed-message bridge between ChatGPT (via MCP) and a local MultiTool/Hermes agent. ChatGPT creates tasks, writes analysis/code and acknowledges reports. The local agent executes explicitly supplied commands and returns reports. PostgreSQL records message order, actor turns, delivery leases and acknowledgements.

```text
ChatGPT -> authenticated MCP edge -> PostgreSQL dialogue/outbox
             ^                               |
             |                               v
       agent report <- local managed Inbox -> MultiTool/Hermes

Owner browser -> loopback Taskboard :8789 -> PostgreSQL
```

## Security and operational boundaries

- Localhost-only Taskboard, password authentication and CSRF/Origin protections for mutations.
- MCP edge uses locally provisioned gateway credentials; secret values are not part of this repository.
- Scoped, time-limited write permissions, independent watchdog and fail-closed agent delivery.
- Durable claim-intent prevents blind repeated SDK execution on ambiguous delivery outcomes.
- The Stage 39 session controller renews *existing* narrow permissions within a hard deadline; it does not recreate permissions that the watchdog has revoked.
- Explicit open permit mode: when armed with `allow_create`, the gateway may create dialogues and answer any non-canary dialogue; the default remains one scoped dialogue.
- First-turn admission: a machine-signed first turn is verified locally before the agent is authorised to write in a new dialogue.
- Owner hand-back: the Taskboard can return the turn to the agent without retracting a delivered message; retraction stays limited to not-yet-delivered messages.

## Audit scope

The source export includes: Python service and SQL routines; MCP edge and guard; managed Inbox plugin; tunnel launcher; Stage 37A permission watchdog and Stage 39 session keeper; Taskboard and UI; deployment scripts/templates; test suites; and a manifest with provenance and exclusions.

**Not included:** actual credentials, cryptographic secrets, database DSNs, SSH private keys, live systemd environment values, session/permit files, runtime logs, internal task bodies, receipts, database dumps, personal data and private repository material.

One-shot stage installers are review material, not instructions to reinstall or replay them. Run nothing on a live system without revalidating dependencies, versions and authorization.

## Known limitations

- Automatic reconciliation for an ambiguous SQL claim outcome remains technical debt.
- Two successive tasks were verified through one Stage 39 session; a continuous session has been running since 2026-11-28, but a full six-hour renewal window under real disruptions has not yet been recorded as evidence.
- A successful message append is not proof that MultiTool executed the task; delivery ACK is distinct.

See the manifest for the exact public-file list, source hashes and exclusions. A public snapshot is not proof of a live runtime state.
