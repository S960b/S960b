# Stage 37B2E: read-only runtime evidence

Read-only check of the active candidate path, configuration SHA, permissions, local seen/inflight, MultiTool processes, and user-owned logs. Does not infer runtime load solely from configuration registration. If no import-success event exists, result is explicitly inconclusive, and the next step is a separately reviewed instrumented startup or controlled task. No PostgreSQL calls, no plugin imports, no tasks, no writes.

Run: `python3 -IB 450_readonly_runtime_evidence.py`.
