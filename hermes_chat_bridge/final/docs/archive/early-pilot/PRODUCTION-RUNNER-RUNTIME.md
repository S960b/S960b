# P3 runtime acceptance package (synthetic only)

This exercises the actual `runner.serve_candidate` on the main thread, not
the earlier host-only `wakeup_smoke`. Run once, after the offline suite:

```
timeout 25s env PYTHONPATH=src python -m git_dialog.runner_runtime --self-test
```

The self-test CLI accepts no state, host, port, credentials or key arguments.
It creates a new temporary directory and private state (700/600); random owner
password and dedicated test HMAC key exist only in memory. No Git transport,
PAT, real history, bridge call, subscription, worker execution or Git write.
The synthetic event's fixed task routing is not a real queued job.

Two sequential actual server lifecycles bind **127.0.0.1 port 0**. The private
`_RuntimeProbe` handshake supplies the effective OS-assigned port and publishes
readiness only after SIGTERM handling is installed. Ports 8787/8789 and
privileged effective ports are rejected. Ordinary runner CLI still rejects 0;
no existing state/service or live code is patched. The probe is trusted
in-process test instrumentation, never an HTTP-selected callback.

Expected status arrays:

- Default-off: `[401,200,404]`, reader attempts periodic=1/wakeup=0.
- Explicit ephemeral test opt-in: `[401,200,401,401,202,405]`, periodic=1/wakeup=1.

Owner Basic does not grant wakeup authority; HMAC does not grant panel-read
authority. 202 means only a pending refresh signal. Successful status must be
synthetic/read_only/error-null/delete-disabled. One reader thread uses only
the serial `loop.tick` path. Requests are bounded, connections closed by the
client and StrictChannel, no HTTP proxy environment is used.

Each phase has a runner-owned 10s watchdog closing only its own socket map.
The client has readiness/request/drain limits and signals only its own process,
through a private lifetime-locked hook; it never signals another PID. The
runner handles SIGTERM on the main thread, ignores further termination during
cleanup, closes its sockets, joins its reader and Waitress workers, cancels the
watchdog and restores the prior SIGTERM handler. Self-test client is joined;
temporary state is removed in `finally`. Total elapsed is checked against 22s;
the outer 25s timeout is required as a final independent bound. A timeout or
failure is a failed acceptance: do not retry or change protections.

This proves the synthetic server entrypoint lifecycle, not interactive CLI
password entry, installed P1 replacement, real Git I/O shutdown, production key
provisioning, live event forwarding or common authority for all writers.
Existing P1 service/CLI and protected 8787/8789/bridge remain unchanged.
