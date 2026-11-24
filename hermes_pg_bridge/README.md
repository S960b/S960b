# Hermes PostgreSQL Bridge — security audit source

[Русский](README.ru.md) · [Previous Git-based bridge](../hermes_chat_bridge/)

**Status (2026-11-24): source inventory/export in progress.** This directory is being prepared for independent review. Until the source and its manifest appear, it is NOT a complete audit package.

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

## Audit scope

The source export is intended to include: Python service and SQL routines; MCP edge and guard; managed Inbox plugin; tunnel launcher; Stage 37A permission watchdog and Stage 39 session keeper; Taskboard and UI; deployment scripts/templates; test suites; and historical implementation stages, with a manifest distinguishing installed sources from archival copies.

**Not included:** actual credentials, cryptographic secrets, database DSNs, SSH private keys, live systemd environment values, session/permit files, runtime logs, internal task bodies, receipts, database dumps, personal data and private repository material.

Historical scripts are review material, not instructions to reinstall or replay one-shot stages. Run nothing on a live system without revalidating dependencies, versions and authorization.

## Known limitations

- Automatic reconciliation for an ambiguous SQL claim outcome remains technical debt.
- Two successive tasks were verified through one Stage 39 session, but full six-hour renewal under real disruptions was not established by that test alone.
- A successful message append is not proof that MultiTool executed the task; delivery ACK is distinct.

See the accompanying manifest and audit notes once export is finished; do not treat this provisional index as a completeness statement.
