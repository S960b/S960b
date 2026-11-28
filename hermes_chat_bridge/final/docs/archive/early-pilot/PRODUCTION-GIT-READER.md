# Bounded Git reader beta

`runner serve --state-dir NEW_CANDIDATE_STATE --mode github` now opts in to a
process-isolated read-only Git snapshot. Synthetic remains default; wakeup is
off unless explicitly enabled. Existing P1/8789 and 8787 are not replaced.
The configured credential-file path is prompted invisibly, validated by the
existing UID/600/regular/NOFOLLOW loader and never put in argv/env/log/DB.
No discovery or credential rights/expiry changes. Never send the value or path
through the bridge. Existing candidate marker and state/flock checks remain.

Each refresh launches exactly one owned child with a fixed module/executable,
no shell, sanitized env (no inherited proxies/credentials) and stdin/stdout
pipes. The token and previous snapshot travel only over this private pipe.
The child uses the existing SnapshotReader + ReadOnlyTransport + JsonHttp:
fixed private repository/task/main checkpoint, full hash/history validation,
head recheck and GET-only. No mutation, job claim, callback or body execution.

The parent budgets the whole operation at 15s (allowed API budget 1..30s),
including sequential HTTP requests, response trickling and DNS in the child.
Cancellation is checked at most every 100ms during communicate. The parent
terminates only its owned child, waits 300ms, then kills/reaps it within 1s if
necessary. No arbitrary PID, service kill, process-group kill or retry. OS
process creation/kill/reaping still depend on a responsive OS; not a hard
real-time guarantee. The output is capped at 16MiB by the trusted child and
checked by the parent, revalidated as JSON/model/task/hash and append history.
There is no pickle. Errors are sanitized, source failure closes the UI (503),
never stale/synthetic fallback. Output buffering is trusted-child bounded,
not a sandbox against compromised local Python/module code.

Host stop signals cancellation BEFORE waiting for the serial reader lock.
Runner shutdown does so before joining its reader. The direct legacy P1 CLI
path remains available and is not silently switched or installed.

Offline acceptance uses fake subprocess handles/clocks/FakeApi model fixtures
for deadline, cancellation, SIGKILL escalation, pipe closure, schema, role
binding and fail-closed state. Actual local process acceptance, once:

```
timeout 10s env PYTHONPATH=src python -m git_dialog.isolated_read_smoke --self-test
```

This creates no DB/listener/token. It starts only the fixed child module twice:
one blocked on stdin before networking (then reaped), one rejecting `{}` before
networking. It does NOT prove authenticated Git network success, live bridge
forwarding, key provisioning, shared write authority or full beta deployment.
Do not claim otherwise. A real Git opt-in run needs local configured credentials;
no secret/path should be requested in chat. No real close/delete is enabled.
