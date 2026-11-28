# Scoped bridge launcher handoff

This is a concrete deployment artifact, not an installer or automatic takeover.
`deploy/hermes-beta-launcher.py` defaults to pinned hash/AST check. Only explicit
`--run` supplies the fixed new beta state and receiver8790 to the P7 bootstrap.
It adds the reviewed package to sys.path without replacing configured environment,
PYTHONPATH, argv or CWD. P7 then preserves original source __file__/sibling imports.

The reviewed user service is hermes-bridge.service, Type=simple, CWD
/home/kali/S960b/research/hermes_chat_bridge, old MainPID830. Its sole fragment is
/home/kali/.config/systemd/user/hermes-bridge.service; there were no drop-ins at
review. Existing environment/credential configuration is deliberately not read.
Original interpreter is /home/kali/.venvs/bridge-clean/bin/python. The fixed
drop-in changes ONLY ExecStart and temporarily Restart=no (previously on-failure)
to prevent a failed first startup loop. It removes APPDIR/APPIMAGE at exec time.
It does not change original source, CWD, environment directives, OAuth/DB routing,
ports8787/8789, P1 or taskboard. Inherited unreviewed runtime dependencies remain
a trust boundary; check-only does not prove actual imports or startup.

Before handoff, an operator must have the exact reviewed Git revision at
/home/kali/agent-dialog-beta/code, preserve a running green receiver8790, and
copy the already generated pinned candidate without overwrite to
/home/kali/agent-dialog-beta/bridge-candidate.py (UID-owned regular600 singlelink,
SHA25630e6307ed09313fcf21fa947380214e5adbb9d2b35442ed88d728c95edbbd11f).
Original context must remain regular644, same UID/singlelink and
SHA256ffbbe5b4252f16544ed9de09f4a715cff2cb4347199866c47ba4584eb726647f.
No regeneration, secret copying, config/environment extraction or credential
tempfiles. The receiver already holds its PAT in memory; bridge uses only its
dedicated existing beta HMAC key through reviewed local load.

Only after check succeeds under ORIGINAL interpreter/CWD, a separate reviewed
operation may place the exact drop-in at
/home/kali/.config/systemd/user/hermes-bridge.service.d/90-agent-dialog-beta.conf,
using a new directory/file only, never overwriting existing files. Immediately
before mutation recheck the reported service identity/CWD/no-dropins and record
only metadata of the original fragment. Do not read its secret-bearing contents.
Keep the original fragment untouched. A concurrently changed layout means STOP.
Then daemon-reload and ONE restart of this exact user service are targeted
authorized actions, not permission to kill unrelated processes or stop the beta
receiver. Failure triggers the prepared rollback, not retries or code repair.

Rollback: move ONLY our exact newly installed drop-in (verify bytes/hash) to a
new non-.conf backup name outside the active drop-in set, daemon-reload, then
ONE restart of hermes-bridge.service. This restores the original ExecStart and
Restart=on-failure from the untouched fragment. Do not remove an unexpected or
changed file, reset all drop-ins, edit the original unit, or delete databases,
source/dirty hunks, keys, candidate, histories or other services. If identity or
rollback prerequisite changes, STOP and report; no broad recovery.

Acceptance: active/running service with new PID, unchanged fragment metadata,
the exact single scoped drop-in, unchanged source hash and working directory.
Forwarder emits only fixed JSON stderr lines forwarder_started and, after a
real scoped event HTTP200/202, receiver_accepted (beta_wakeup field). These lines
contain no key/signature/body/job identifier/path/error/response contents.
Log-output failures are isolated. Use only a bounded exact-marker filter for
this service's current startup, never dump journal/config/environment contents.
The real MT report event can be the first natural forwarding observation; do
not create a synthetic job, re-emit old jobs or call claim APIs as observers.
Missing receiver_accepted is NOT proof of failure before a matching event occurs.
Its presence is proof of an accepted wakeup hint, NOT execution ACK, writer
authority, authenticated-owner action, physical erase or exactly-once delivery.
The sidecar API still labels itself passive_sidecar_no_bridge_changes; that is
its static authority label, not a runtime audit of the external launcher.

Keep the event webhook enabled throughout. The bridge restart may temporarily
interrupt the connector: do not resend an uncertain assignment or invent jobs.
Beta remains read-only; common authority of all writers is not deployed.

P8 actual handoff failed before forwarder_started; the reviewed rollback restored
original service MainPID656263/Restart=on-failure/no active drop-ins. The drop-in
directory now EXISTS empty, and rollback.conf and durable pinned candidate also
EXIST: do NOT repeat the earlier new-path creation/copy assumptions or overwrite
them. The original append stdout/stderr sink is inherited, so journal-only marker
filtering was not a sufficient observation method. Do not dump that sink or
extract its unknown path/config; only an already-known local operator path and
future explicitly scoped fixed-token filter may be used after review.

The launcher now accepts explicit --validate-state for the fixed beta state,
without --run and without original bridge execution. It is a diagnostic operation,
not a service handoff, key provisioning or permission to retry a failed startup.
MT's chmod repair of newly copied mode664 drop-in violated STOP-on-mismatch;
record the deviation and do not repeat or silently repair future mismatches.

After the P9 controlled retry, build_app/RuntimeError was observed and the native
service restored again. Known source has an auth-env guard AND RuntimeError
paths in pilot_database_lock: the phase alone does not identify the fired guard.
Do not continue replacing ExecStart or extract missing credentials. See
PRODUCTION-NATIVE-BRIDGE.md for the candidate-only native-entry-preserving
approach; no live installation/activation is implied by its code or tests.
