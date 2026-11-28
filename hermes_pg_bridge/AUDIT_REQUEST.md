# Independent security and reliability audit — Hermes PostgreSQL Bridge

**Scope:** review the code snapshot and manifest under this directory. This is an authorized review of a system controlled by its owner. Do not interact with real credentials, live hosts, private dialogues or external services; prefer static analysis and sandbox tests.

**Snapshot date:** 2026-11-28.

## Components to review

1. PostgreSQL schema, SECURITY DEFINER functions, role grants, transaction boundaries, actor turns, message sequencing and idempotency.
2. MCP edge/guard, credential injection, JSON-RPC allowlist, header and method checks, host/port binding and failure modes.
3. MultiTool managed Inbox plugin: claiming, intent-before-claim, local journal fsync, uncertain claims, replay prevention, delivery ACK, crash recovery and live-lock behavior.
4. Scoped write permit (single-dialogue scope and explicit `allow_create` mode), local agent gate, Stage 37A watchdog and Stage 39 time-bounded operator session keeper (timer/renewal races and fail-closed behavior).
5. Tunnel transport and systemd service templates: process identity, privileges, local sockets, restarts and cleanup.
6. Loopback Taskboard, Basic authentication, CSRF/origin checks, delete/return-turn/hand-back semantics, read amplification and browser caching.
7. Historical installer/transition stages: dangerous one-shot assumptions, non-idempotent writes, hidden dependency on past state and rollback consistency.

## Required audit questions

- Can any remote or local principal acquire an actor capability they should not have?
- Can a forged/replayed message, wrong actor, concurrent claim or crash trigger duplicate SDK execution, lost ACK or false delivery?
- Are permissions safely scoped and revoked under time drift, process crashes, config SHA drift, watchdog failure or tampering?
- Are SQL SECURITY DEFINER routines safe from search_path, privilege escalation, cross-dialog access or missing locking?
- Could arbitrary MCP methods or unexpected protocol revisions bypass a guard or cause denial of service?
- Can the UI expose private dialogues, modify state without authorization, or misrepresent completion?
- Are there credentials or sensitive host data in any source, templates, tests, packaging logic or commit history?

## Reporting format

For each finding: ID, severity, confidence, precise file/line evidence, exploit prerequisites, attack path, demonstrable test or proof, impact, minimal fix and regression test. Distinguish verified vulnerabilities from conjecture. Provide both a list of open questions and a prioritized fix plan.

## Known open debt — do not treat as resolved

- The Stage 37B2C intent-before-claim logic fails closed on uncertain SQL outcomes. Fully automatic safe reconciliation was not established; historical manual recovery required explicit evidence and ACK.
- Stage 39 was validated with two successive tasks in one session; a continuous session has been running since 2026-11-28, but full-window renewal and fault recovery still require recorded evidence.
- A public code snapshot excludes real secrets, logs, private message bodies, live PostgreSQL state and runtime configuration. Absence of these is not evidence that the operational deployment exactly matches the snapshot.

**Never run historical installers, test write paths or real agent commands on the live system while auditing.** Any proposed dynamic test needs an isolated instance with fake credentials and fake conversations.
