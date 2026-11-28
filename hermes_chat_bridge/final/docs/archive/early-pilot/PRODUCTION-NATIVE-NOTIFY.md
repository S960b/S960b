# In-process notification for an existing report

P13/P13b operator facts explain the observed zero: MT wrote reports with direct
Queue.put_message plus raw SQL delivery inserts, bypassing emit_test_event.
Existing bridge_cli.py and worker._emit_event instead construct fresh BridgeApp
objects, whose _beta_forwarder is None. The running service has the forwarder,
but no existing code in bridge_server.py calls emit_test_event. Thus restarting
the receiver or reading its PTY cannot repair this report-transport bypass.

This revision does NOT add a report-content writer, Queue.put_message, raw SQL,
Git write, Work-claim, execution endpoint, new credential or shared authority.
It adds ONE signed notification containing only an already-created job ID,
timestamp, fixed queue=test and fixed pilot task. The live service checks its
existing queue metadata for the same ID, task, queue, to_chatgpt and pending,
then invokes the existing emit_test_event in that service process. The event
producer still does normal legacy subscription enqueueing plus nonblocking
forwarder.offer. Report text is neither an endpoint input nor authority.

The client uses ONLY literal127.0.0.1:8765/bridge/beta-notify, dedicated existing
beta HMAC with a distinct protocol domain prefix, a total2s socket deadline,
8192-byte response-header cap, no response-body output, proxies, DNS, redirects,
Basic auth or credential discovery. Missing local opt-in marker returns false
without reading the key. Valid opt-in requires the same zero-byte600/currentUID/
singlelink marker and the ready existing private key loader. No provisioning.

The server requires loopback peer, exact Host/rawpath, no query/Origin/proxy/TE/
Expect or duplicate headers, content-type application/json, bounded body1024,
2s body deadline and HMAC BEFORE strict JSON decoding. Freshness120s/future30s,
bounded in-memory replay180s/max256 and30s rate limit. Authentication for this
notification grants no owner panel role or arbitrary queue/message operation.
Callback exceptions return only503 unknown; no raw exception/text/path output,
no auto-retry after reserved attempts, no durable exactly-once claim. Queue
status can race a Work-claim; pending-only rejection remains fail-closed.

NotifyASGI is a plain ASGI callable. attach uses the original router.add_route
and moves the exact path ahead of a possible existing '/' mount. Other routes
are not replaced. It attaches ONLY when the existing native forwarder is
configured. No new runtime library is installed by this code.

notify_patch creates two SEPARATE NEW O_EXCL600 candidate files only. It reads
ONLY fixed known bridge_server.py SHA2564921809e5aa9e7a67f7bee77f355d58123b076507d9d8f54da6b67dd4451c585
or bridge_cli.py SHA2560d2cdcb6e034b05a3526c55d7dd0efa2cead886b2fdc82b6f9d3165272e63d67,
regular644/currentUID/singlelink/NOFOLLOW/bounded512KiB, with identity/AST guards.
It never imports/executes the source or installs/restarts anything. Every old
source byte is retained. Bridge hook attaches after configure_beta_wakeup,
inside its existing lock-cleanup try. CLI hook is scoped to this task/test and
placed AFTER Queue pending success but BEFORE fresh BridgeApp/legacy emit.
Its temporary fixed package sys.path is restored in finally, and notification
failure never prevents the original publication. Worker is NOT modified.

Example candidate-only operations, each with a distinct new output:

```
PYTHONPATH=src python -m git_dialog.notify_patch --kind bridge --output NEW_BRIDGE_CANDIDATE
PYTHONPATH=src python -m git_dialog.notify_patch --kind cli --output NEW_CLI_CANDIDATE
```

Actual output hashes must be operator-generated/reviewed BEFORE a separate
hash-pinned install/backup/rollback package. Old P11 installer targets the old
native hash and MUST NOT be repurposed to install these files. No candidate
is executed or installed by this revision. Source permissions previously
changed by MT outside STOP constraints remain recorded deviations; these
tools never chmod/repair an existing file.

Offline tests exercise pure ASGI with an in-memory router fixture and fake
socket, not a real listener. Local Starlette dependency installation was
blocked and was not retried via another installation route. The optional
ready `python -m git_dialog.notify_smoke` uses an ALREADY-installed Starlette
in the original known runtime, synthetic key/queue only, in-memory ASGI
requests, and fixed JSON401/202/404 proof. No real bridge import, state/key read,
DB, network or listener. This integration proof must be reported separately
from unit tests, and must be green before live installation is considered.

An HTTP202 notification means the fixed producer callback returned, NOT that
the beta receiver accepted a wakeup, a Git write happened, or a job executed.
MT must eventually use the reviewed patched CLI for natural reports instead
of raw SQL; until that deployment and natural-event proof, delivery is NOT
accepted. Shared write authority for ALL writers and real delete remain off.

## P14b CLI candidate correction and safe failure phases

MT accepted689tests and the real installed-Starlette in-memory proof401/202/404,
then created bridge candidate2714aca2a7420cd2ff073baee498302f63ade28e49051ca546c69b62ddbb9ce5.
CLI generation failed with the old generic error, without creating its output.
Source hashes and metadata were reported unchanged. Its exact failure branch
was NOT established; ctime/race explanations are unproven.

A local regression reproduced a separate concrete generator defect: global
uniqueness of a BridgeApp constructor rejects a CLI with an unrelated bridge
constructor in another branch. Selection now first identifies the unique
report Queue put statement list, then requires a unique constructor and exact
pending guard/order INSIDE that same direct block. Ambiguity inside the report
block still fails closed. File/hash/permissions/ownership guards are unchanged.
This corrects that reproduced defect, not a claim that the MT failure was
independently traced to it. Any remaining failure returns only an allowlisted
phase such as cli_put_anchor/cli_bridge_anchor/cli_pending_guard/cli_order,
never exception text, paths, source lines or values. The bridge transform and
notification runtime are unchanged. Preserve the already-created bridge
candidate; the next reviewed operation generates only a new CLI candidate.

## P15 dedicated pinned pair installer (separate from P11)

MT P14b accepted693tests and validated BOTH actual candidates without live
changes. Bridge SHA2562714aca2a7420cd2ff073baee498302f63ade28e49051ca546c69b62ddbb9ce5
(67403bytes/1288lines) and CLI SHA256837ba7279b4f523c53d44a0139f9e5e3d539bf96c9821714d7bed5586d0e1048
(4523bytes/108lines), both600/currentUID/singlelink/no-symlink, syntax-only
compile0. Original hashes492180... and0d2... remained unchanged. Existing
real-runtime in-memory ASGI smoke401/202/404 is accepted, not rerun.

`git_dialog.notify_install` independently pins these two BEFORE/AFTER hash pairs.
It reuses only reviewed low-level filesystem guards from native_install, NEVER
the old P11 install/activation operations. It has check/install/rollback actions;
check/install require both already-existing candidate paths. No regeneration.

All directory components must be real directories; source/candidate parents
must be currentUID/non-group-or-world-writable, BASE/STATE exactly700. Sources
must be regular644/currentUID/singlelink/NOFOLLOW/bounded512KiB/exact hash/stable
identity. Candidates must be600 with the same identity/metadata guards. The
existing activation marker must be regular600/currentUID/singlelink/zero bytes;
its inode and identity are rechecked, never changed. No key/verifier/DB/config/
unit/env/process/log/service/network access. Existing P11 backup is untouched.

Check is read-only. Install creates NEW O_EXCL600 byte-exact backups
bridge-before-notify.p15.py and cli-before-notify.p15.py under BASE, then NEW
same-parent644 stages .bridge-notify.p15-stage and .cli-notify.p15-stage. It
fsyncs/verifies BOTH backups and stages BEFORE replacing anything. Source and
marker identities are rechecked before EACH replacement. Bridge is replaced
first, CLI second, with parent fsync and verification. Existing files are never
chmod-repaired; fchmod applies ONLY to new inodes. No service restart or runtime
handoff occurs in this module. Installing the CLI does NOT authorize switching
the report channel. Durable package update and restart require separate exact
reviewed commands; the new installed bridge must not start with old P11 code.

Rollback accepts only BOTH installed AFTER hashes plus BOTH pinned BEFORE
backups and the unchanged active marker. It first retains NEW600 archives
bridge-notify.p15-rollback.py/cli-notify.p15-rollback.py and NEW644 restore stages
.bridge-notify.p15-restore/.cli-notify.p15-restore. It restores CLI first, bridge
second, preserving all backups, archives, activation and P11 native behavior.
It does NOT restore the older pre-native original, deactivate wakeup or restart.
Rollback is implemented/tested, NOT silently authorized on errors.

This is NOT an atomic two-file transaction or a universal CAS. Serialized
operator use/no concurrent writer is required. If any phase fails, safe fixed
JSON reports action/phase/completed_replacements (including rename-before-fsync
failure), retains every artifact and STOPS. No cleanup, retry, automatic undo,
overwrite, guessed completion or replacement-path search. A partial install
cannot use full rollback: it needs a separately reviewed recovery based on the
exact completed replacements and current hashes. Never rerun to force progress.
The current ready stage tests and checks the installer; actual installation,
durable-code update, restart and reporter switch are separately scoped.
