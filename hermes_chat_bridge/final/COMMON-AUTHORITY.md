# Common write authority: integration contract and rollout

## Current state (P19 code, not a deployment claim)

The native notification transport is installed. It is not the Git write
authority. The existing owner panel and beta receiver remain read-only.

The existing code already had actor-bound WriteGateway ports, a shared
LifecycleRegistry, pure WorkerStep actions, an execution journal, confirmation
controller and an offline authenticated Dispatcher. Canary programs were
separate historical probes with direct GitHubDialog access. There was no
durable publication grant for an ordinary ChatGPT/MT append: only the worker's
saved-result publication path had that guarantee.

This revision closes that backend gap without adding another Git writer or
credential scheme. CommonAuthority composes the EXISTING gateway/registry/
ingress/worker and a single new AppendStore. Trusted host bindings map ChatGPT,
MT, worker and each canary actor to ports on the SAME gateway and durable store.
The caller cannot select an actor, task, role or repository through message
JSON. The binding name is host configuration, NOT authentication by itself.

| Caller | Bound actor | Required entry |
| --- | --- | --- |
| ChatGPT | chatgpt | common.port('chatgpt') or common.dispatcher |
| MT | mt | common.port('mt') or common.dispatcher |
| pure worker | mt | common.worker (requires shared durable port) |
| isolated canary, request side | chatgpt | common.port('canary_chatgpt') |
| isolated canary, response side | mt | common.port('canary_mt') |

All ports hold the same lifecycle transaction across authorization/publication.
Inactive/cancelled/closing/tombstoned tasks block every configured writer BEFORE
network I/O. Common dispatcher construction also verifies that the owner
confirmation controller has the exact same registry and underlying dialog;
it cannot silently close a different task/registry. Existing owner confirmation
only prepares an intent, not a remote delete. Delete remains disabled.

## Durable append semantics

AppendStore is a separate private600/currentUID/singlelink SQLite file under a
private700 parent. It never opens the bridge DB, existing canary journals or
owner credential store. Wrong databases are rejected without schema migration.
All actor connections must point to the same file; use one connection per owner
thread and preserve the file across restart.

Before mutation the gateway validates the snapshot and saves the exact message
and expected head. A one-time attempted flag is committed with synchronous FULL
BEFORE any remote object creation or ref update. Afterwards it observes the
exact message independently, not an ACK alone. The same immutable message ID
cannot acquire another grant, even after restart, a timeout before object
creation, or a lost PATCH response. Retry of an attempted ID means read-only
reconcile; absence returns gateway_publication_unresolved, NEVER resend.
Saved timestamp/payload are not recomputed. Different actor/kind/body/timestamp
under the same ID is a conflict. Status projection excludes body and local paths.

The worker retains its existing execution journal (no recomputation of unknown
effects). Its report now has BOTH the execution publication reservation and
the common append grant; a crash cannot bypass either. Existing historical
probes remain historical, not commands to rerun on seq6 or a deployed fallback.

## What must still be connected before owner write testing

1. One authenticated runtime host must instantiate CommonAuthority for the
   existing fixed task and durable files. Route the actual MCP ChatGPT ingress,
   installed MT reporter and worker through these ports. Do not accept role or
   actor from a request body, reuse panel passwords as agent tokens, or expose
   an unauthenticated session/grant issuer. Existing AccessAuthority/Dispatcher
   are the boundary to reuse, not a new parallel protocol.
2. Disable the cooperating writers' direct dialog-ref publication paths before
   enabling runtime mutations. A Python wrapper CANNOT revoke another holder's
   GitHub credential, constrain the ChatGPT GitHub connector, or fence external
   force/reset/recreate. Runtime coverage must be verified, not asserted by a
   checkbox or the mere presence of these modules. Git-backed source-code
   publication on the implementation branch is distinct from dialogue writes.
3. Run one isolated canary through the SAME runtime authority, then let the
   owner test the authenticated UI. Do not rerun fixed historical canary seq/IDs,
   mutate the existing dialogue merely to force proof, or enable lifecycle
   delete/create/recover automatically. Until the adapters are actually wired,
   capabilities explicitly report remote_adapter_integrated=false and
   external_writers_fenced=false. The live panel remains read-only.

These are concrete remaining integration tasks, not transport/log-proof loops.
P19 is the shared backend implementation and offline full-chain acceptance;
it is NOT proof that all live writers already use it. Never label it full beta.

## Verification

The offline tests bind both authenticated ingress actors, worker and canary
ports through the common store, verify separate-connection visibility of the
attempted grant BEFORE the first write, lost-response recovery without resend,
shared cancellation, duplicate/conflict handling and unsafe database rejection.
Network/subprocess are forbidden and SQLite is restricted to pytest tmp_path.

Run the full suite and compile src/tests/deploy. There is no new dependency,
listener, key provisioning, credential lookup, live Git append, service restart,
activation change or existing state migration in this revision. Runtime
installation needs a separate exact reviewed package after these tests.

## P20 concrete adapter boundary (not live installation)

`CommonAuthority.application(...)` now creates `GatewayApplication`, a real WSGI
adapter for the existing POST `/api/task` dispatcher. It requires the durable
AppendStore, literal loopback origin and a separate port (not 8765/8787/8789/8790).
Authentication is checked before input access and again immediately before
dispatch. Only view/append are exposed: even offline owner close/confirm/cancel
operations are denied here. There is no login/grant issuer or new credential
scheme. Duplicate/framing rejection must occur before WSGI normalization using
the existing `wakeup_smoke.StrictChannel`; it is not possible to reconstruct
duplicate headers from a WSGI environment afterwards.

The application must be constructed AND called on the same single serving
thread that owns registry/append/confirmation SQLite connections. Cross-thread
use is rejected, not repaired with check_same_thread=False. Do not mount a
main-thread-created application directly in the existing two-thread receiver.

`gateway_adapters.GatewayClient` is one bounded literal-loopback HTTP attempt,
without proxies, redirects, retry or Git/SQLite/Queue access. `MCPAdapter`
provides typed dialog_view/dialog_append handlers; `register_mcp_tools` explicitly
registers them via the SDK tool decorator. The trusted existing MCP host must
authenticate/authorize its configured principal BEFORE these handlers run.
Never expose a global agent grant to arbitrary users of a shared MCP server.
No actor/task/principal/session argument is exposed to the model. Session grants
expire/revoke under the existing AccessAuthority; host refresh needs real
authenticated authority, not a tool-call-based grant issuer.

The NEW CLI entry is `python -m git_dialog.gateway_adapters --origin ORIGIN`.
One stdin JSON envelope has exactly session/csrf/request; the request is the
existing Dispatcher view/append schema. Tokens never come from argv/env/files.
This is NOT the installed Hermes bridge_cli.py: it does not replace report
Queue writes, carry BRIDGE_DB_PATH, or silently turn Hermes jobs into Git-dialog
messages. No fallback writer exists. Uncertain response returns a fixed error
and retry_authorized=false; preserve the exact request/message ID for reviewed
read-only reconciliation, not a fresh publication.

Offline integration exercises actual client serialization, WSGI dispatch,
MCP registration/handlers and CLI stdin parsing through one authority/store,
using only a fake HTTP connection and fake Git remote. It is NOT an actual
socket/Waitress/installed-MCP acceptance. No fixture is bypassed. The deployed
bridge's MCP registration/version/auth/serving-thread contract must be known
before a byte-pinned live patch can be built; SDK public docs alone do not
establish what the installed server runs. These adapters do NOT yet constrain
the live connector or its GitHub credential. Capabilities remain false for
remote_adapter_integrated/external_writers_fenced; delete stays off.

## P21 approved binding and runnable integration

The owner explicitly approved the proposed authorization binding on 2026-10-29:
existing OAuth owner check for the MCP ingress, fixed ChatGPT role; private Unix
socket with kernel peer UID for the local MT CLI; one existing lifecycle registry
and AppendStore. This is not approval to discover/reuse unrelated credentials,
erase/reset registries, mutate OAuth subscriptions or enable lifecycle deletion.

`unix_authority.Runtime` now owns the existing Dispatcher and CommonAuthority
on one serial SQLite-owner thread. Its AccessAuthority grants are purely
internal, issued only after trusted peer binding and immediately revoked;
there is no public grant issuer, shared client token or credential file.
`Dispatcher(confirmation=None)` is an append/view-only binding, with lifecycle
requests explicitly unavailable. Registry existing-mode rejects missing/empty/
wrong-application stores rather than silently creating a new active registry.

The foreground `unix_authority serve` runtime exposes TWO new private600 Unix
nodes under the existing private700 state directory. The ChatGPT endpoint
accepts only the explicitly configured bridge PID + current owner UID. The MT
endpoint accepts current owner UID but rejects that bridge PID. The MCP handler
calls the existing `bridge.require_owner()` BEFORE connecting, on every call.
The reviewed native bridge insertion registers typed dialog_view/dialog_append
after the unique make_mcp_server assignment, before the SDK creates its ASGI
app. The async handler moves only the IPC wait to a helper thread; all SQLite
and Git mutations remain on the runtime's one owner thread. The original ASGI
server and OAuth provider stay unchanged. No WSGI mount into the ASGI server.

This trusts reviewed native bridge code and the local owner. PID binding is not
cryptographic identity, a defense against owner-process compromise, a PID-reuse
guarantee or revocation of outside Git credentials. If the native bridge PID
changes, the gateway must be stopped/reviewed/relaunched with the new observed
PID; never automatically rebind or infer authority from a job body. No claim of
universal CAS/exactly-once/ABA protection or all external writers being fenced.

`python -m git_dialog.unix_authority cli` accepts exactly one Dispatcher JSON
request on stdin and has NO actor/task/UID/socket/credential options. Requests
are one length-bounded frame, with a total receive deadline. No retry/fallback:
on unknown outcome retain the exact request/message ID for reviewed reconcile.
Connect/upload are limited to five seconds; the response frame has a separate
120-second total receive budget because validated Git history uses multiple
sequential requests. This does not bound or cancel the server's Git work: a
client timeout still means unconfirmed publication, never permission to resend
or use a new message ID. Large/slow histories can still exceed this budget.
The existing Hermes bridge_cli.py remains unchanged; Hermes Queue jobs are not
silently converted into Git dialogue messages. Implementation-branch source
publication remains separate from dialogue publication through this gateway.

Production serve requires an already-known EXISTING lifecycle path and a new
append store path. It never searches for a registry, registers a task, recreates
state or adopts a blank DB. The normal read_configured_token loader reads only
the already-configured repository PAT file privately supplied through a local
TTY getpass prompt. The value AND actual credential filepath must never appear
in bridge/Git/log/argv/env/DB; no discovery, process extraction, new secret file
or expansion of token rights/expiry. If that existing binding/TTY is unavailable,
STOP, not a workaround. Git HTTP now explicitly disables environment proxies.

`gateway_patch` pins the installed bridge BEFORE SHA2714aca2a7420cd2ff073baee498302f63ade28e49051ca546c69b62ddbb9ce5.
It performs only the deterministic eight-line registration insertion, preserving
all original bytes/dirty hunks. Check is read-only; candidate writes only a new
600 output. Explicit install retains NEW600 bridge-before-gateway.p21.py and a
NEW644 same-parent stage before rechecking source identity and replacing it.
Rollback accepts only that calculated AFTER + the exact BEFORE backup, retains
a NEW600 archive and NEW644 restore stage and restores the BEFORE notification
bridge, NOT the pre-native original. Any failure returns fixed phase and actual
completed replacements, even a rename followed by fsync failure. Retain partial
artifacts, STOP; no automatic retry/cleanup/rollback or service action. Old P15
installer/rollback is not applicable to these new hashes. Existing backups,
activation marker, 91 drop-in and all protected services remain untouched.

## P21 verification and exact deployment order

The pytest suite remains offline, no socket/subprocess fixture overrides, only
temporary SQLite. `authority_acceptance --self-test` is a SEPARATE explicitly
bounded real-Unix-IPC harness: only its own temporary directory, in-memory
dialogue, fake OAuth callback and two owned forked children; no Git network,
PAT/key/live service/old canary ID. `--installed-sdk` additionally registers the
two typed handlers with the already-installed mcp package; never install a new
SDK to make this pass. It is not a live OAuth or Git conformance proof. The test
checks both roles through the same runtime, deny-before-IPC auth, MCP PID unable
to act as MT and shutdown of owned children/socket nodes. If Unix sockets are
denied in the execution environment, record that blocker and do NOT bypass it.

After local regression, the operator's ONE exact packet first verifies the
code SHA/tests, runs that standalone acceptance in the original interpreter,
and checks the already-known existing lifecycle binding + absent new artifacts.
Only explicit reviewed deployment may fast-forward durable code, install the
one-source insertion, perform one native restart, record its actual new PID and
start the foreground gateway via the approved private credential loader.
No automatic restart/rebinding, new OAuth client/secret, HTTP/counter loops,
existing dialogue canary append or protected8790/8789/8787 restart is included
by these code modules. Real MCP tool availability and one SAME-gateway isolated
acceptance must be reported, not inferred from an offline fake. The owner can
then test append/read through the new tools/CLI and observe the existing panel;
delete/create/close/cancel/recover remain unavailable. Do not label merely
published code as installed/full beta or existing direct credentials as fenced.

## P21 actual acceptance and registry provenance blocker

The operator reported the exact P21 revision passing 844 tests and the standalone
real Unix/installed-mcp2.3.0 acceptance. The existing lifecycle binding was NOT
known, so registry check C stopped and installer check D was NOT run. No live
install/restart/serve occurred. Repeating A/B cannot resolve this missing binding.

The historical `worker_probe.run_probe(..., directory, ..., 'prepare')` explicitly
created `directory/lifecycle.sqlite` alongside `directory/journal.sqlite`, then
registered the fixed existing task. The directory was an operator-supplied
argument, not a production default recorded in this repository. The code proves
the creation contract, not where that historical run placed its files or their
current existence/state. Do not infer a path from the panel/bridge DB or silently
make a fresh active registry: prior cancelled/tombstoned state may be elsewhere.
Recover an already-recorded nonsensitive binding, or obtain explicit scope for
locating/reviewing that historical registry. No broad filesystem/credential/log
search or new registry initialization is authorized by the acceptance result.

## Native lifespan host candidate (not installed, credential binding required)

The owner subsequently directly reported that the fixed `lifecycle.p21.sqlite`
is created/active, the P21 registration insertion is already on disk at bridge
SHA066f9fbbb38b8009112456ea61beab76f9ab4a740eeb8a9d8cc62b5bfb5b43df,
and the running bridge has NOT restarted into that insertion. This establishes
the requested current binding for the next reviewed stage; it does not turn the
earlier out-of-packet search/creation/installation into original compliance or
prove that no earlier registry ever existed. Keep all old artifacts unchanged.

`gateway_lifespan.NativeGatewayHost` and `install_lifespan` now implement the
missing host lifecycle. Bind AFTER the bridge's original lifespan is installed.
The wrapper enters that original context first, starts the gateway before ASGI
readiness, and always stops it before leaving the original context. Thus pilot
lock/notification cleanup still runs if gateway startup fails. One dedicated
serial thread creates the registry connection, factory-provided Git dialog,
AppendStore, CommonAuthority, Runtime and both Unix endpoints, serves every
request, and closes them in reverse order. Only IPC waits/joins leave the ASGI
loop; no SQLite connection crosses threads. The peer PID is the actual hosting
bridge's PID, not a saved PID guess. Existing UnixServer owns/inode-checks its
two socket nodes on shutdown. Shutdown timeout is explicitly unconfirmed, not
a false claim that a blocked Git request was cancelled or cleanup completed.

`open_append_store` creates/fsyncs the absent private600 append DB under the
existing private700 state directory, or validates/reopens only an existing
correct-application store. Empty/foreign DBs and symlinks are not adopted;
artifacts survive failed startup, with no automatic delete/reset/repair. Valid
registry/store reopen no longer rewrites application_id/schema. Retired
registry state still blocks startup before credential loading or store creation.

This candidate deliberately REQUIRES a trusted startup `dialog_factory`. It does
not look for a PAT in bridge OAuth, HMAC, another process, config/env/history or
old files. The previously implemented foreground server's getpass loader cannot
be silently moved into a systemd startup without a terminal. No reviewed
noninteractive Git credential binding has yet been provided, so no production
call-site insertion, live restart authorization or claim of working tools is
made by this module. Test factories/fake Git do NOT fill that live binding.
An explicit private credential-bootstrap decision is necessary before the final
exact native insertion and restart/recovery packet can truthfully be ready.

Rollback ordering must also follow the actual source: existing gateway_patch
rollback accepts SHA066f9fbb... and restores notification bridge2714aca2... using
the retained P21 backup. A later lifespan source insertion needs its own pinned
backup/rollback back to066f FIRST; invoking old rollback directly on an unknown
new source must fail. Do not issue that chain before the new exact bytes and
credential bootstrap are reviewed. No rollback/restart is executed here.

## P22 approved service credential and actual native call-site

The owner explicitly authorized completing the isolated beta, including the
proposed systemd credential/drop-in/daemon-reload mechanism. Do not ask again
for this same authority. Credential discovery, unrelated services, new token
rights, plaintext secret output and fabricated readiness are still forbidden.

`gateway_credentials install` privately prompts on a local TTY for the ALREADY
configured repository PAT source file. It validates a stable regular600 owner
single-link file, accepts only a normalized absolute injection-free path, and
creates a NEW private600 credential-only92 drop-in via a new fsynced stage.
It does not alter ExecStart, environment values, CWD, output, Restart or91;
it does not restart/reload services. The source is not copied into this repo
or a new application credential file. The actual local source path is in that
private systemd configuration, never the tool's stdout/report/hash/Git/DB.
Do not print/read back the drop-in outside this ready installer. Systemd's
normal credential delivery may create its managed read-only runtime copy;
no claim of universal log absence, unswappability or physical erasure is made.

The production factory reads ONLY `CREDENTIALS_DIRECTORY` and its fixed named
credential `authority-git-pat`, accepting owner400/600 single-link regular files
under guarded owner500/700 directories. This is the service manager's defined
interface; the variable contains a directory path, NOT a PAT value. No TTY at
service startup, password/HMAC/OAuth reuse, credential inventory, proxy lookup
or fallback. It creates the real existing GitHubDialog with explicit no-proxy
JsonHttp; all remote work remains on the SQLite owner thread. Missing/bad
credential fails startup before append-store/socket creation.

`gateway_runtime_patch` pins the ACTUAL current066f9fbb... source. It inserts
exactly three static lines AFTER the unique native
`app.router.lifespan_context = lifespan`: imports the approved factory and
lifespan wrapper and binds them. This preserves the original lifespan and all
P21 registration/notification/OAuth/dirty bytes. Shape mismatch stops, never
guesses. Check calculates the deterministic full AFTER hash. Install retains
NEW600 bridge-before-gateway-runtime.p22.py (066f bytes) and NEW644 same-parent
stage, fsync/verify/stable-identity check then one replacement. Rollback accepts
only calculated AFTER plus that exact backup; retains NEW archive/restore and
returns to066f. It never handles service actions or removes registry/store.

The bounded standalone `authority_acceptance --self-test --installed-sdk
--native-host` tests the NEW NativeGatewayHost owner-thread lifecycle, not just
the old direct runtime. Only owned temporary state, memory dialogue, fake OAuth
callback, actual installed SDK registration and real SO_PEERCRED; its trusted
test bindings are not body/production role selectors. No Git credentials or
network. Do not run it inside/bypass the offline pytest fixture. The operator
must run it once on the original runtime; local AF_UNIX denial remains a STOP.

Exact live packet order: new code acceptance; native-host IPC acceptance;
safe current native properties/version/registry-destination/source/drop-in
preflight; durable fast-forward preserving venv/untracked; private credential
drop-in provision; pinned source install; ONE daemon-reload/normal native
restart; ONE bounded startup wait/properties and `gateway_health` read-only MT
view. Health reports only fixed Git head/count/next actor and known socket/store
metadata, never message bodies, paths, credentials or another test message.
It is not an authenticated ChatGPT append or global writer-fencing proof.

Strict conditional recovery only after a fully completed install and failed
restart/startup/health: stop ONLY native bridge, require successful stop, new
runtime rollback AFTER->066f, then old gateway_patch rollback066f->2714, archive
ONLY owned92 credential drop-in, daemon-reload and ONE recovery restart. Every
step must succeed before the next. Preserve latest durable code (old notification
modules remain compatible), registry, append DB, all old backups and protected
services. Failed/partial installers always STOP, no retry/cleanup/automatic
rollback of partial artifacts. No old rollback directly on a new source hash.
Actual exact limits/commands belong in the reviewed execution packet.

Next evidence after startup is real authenticated ChatGPT dialog_view/append
through the newly installed tools and the same AppendStore, then reviewed MT/
worker/canary adoption. Existing Hermes queue reporting is still transport, not
the Git authority; no new hidden Queue/SQL writer or automatic relay is added.
Do not claim step4/5/6 or full beta from source installation/health alone.

## P23 cooperating worker/canary adoption

P22 is now installed and the real OAuth ChatGPT append was confirmed by a
subsequent Git history read: message gateway_handoff_p22_20261029, seq7,
commit40361b84ca6405d2f440db92b50f6ed3392a2d7a. Client tools are available;
no further Refresh/authority approval is needed. That ID is consumed, not a
reusable probe. These are reported/observed checkpoints, not future promises.

The existing verified MT Unix route now admits fixed capabilities worker_step,
canary_report and writer_status. They cannot select an actor/task/path/policy.
CG peers cannot invoke them. Canary is a fixed MT inert report through the
same CommonAuthority port and AppendStore, not the historical canary scripts.
The CG half remains the real OAuth dialog_append. Worker runs the existing
strict CommonAuthority.worker on the native SQLite-owner thread, with only
the trusted ping/sha256 policy. No shell/model/callback/subprocess/tool runner.
An ordinary prose handoff is rejected before journal creation or computation;
this capability never polls or automatically treats all tasks as executable.

The fixed private authority-worker.p23.sqlite is provisioned LAZILY only for
an explicit valid worker step. Empty/foreign/symlink files are not adopted,
existing legacy journal migrations are not invoked by this live binding.
File/parent are guarded and fsynced. Normal close/reopen stays on the owner
thread; no journal/registry reset or stale-socket deletion. Worker start/result
and publication reservation survive failure. Same IDs after unknown can only
observe saved publication; started/unknown cannot recompute. AppendStore grant
is committed before network mutation, in addition to the execution ledger.

Local `python -m git_dialog.authority_writers canary|worker|status` consumes ONE
bounded stdin JSON request, then uses ONLY the existing MT socket. No credential,
Git, SQLite queue, fallback or automatic retry in this client. It accepts no
actor/task/path/policy options. All uncertainties preserve the exact IDs/input.
The old live_probe, journal_probe and worker_probe CLI entry points now fail
before reading credentials. Historical library fixtures remain for offline
regressions; this is not universal removal/fencing of all credential holders.
Existing Hermes Queue reporting remains transport and is NOT disabled here:
removing it before a reviewed replacement would strand operational reporting.
No hidden SQL bridge or automatic Git-to-queue relay is added.

New standalone `authority_acceptance --self-test --installed-sdk --native-host
--writers` exercises an actual temporary NativeGatewayHost and real Unix peers,
new pure worker task/result and inert canary handoff through the SAME outbox.
It is separate from offline pytest, never overrides its fixture, uses no Git
network/PAT/live state and never repeats historical canary IDs. Local socket
denial remains a STOP; the original-runtime operator provides this proof.

The reviewed live stage first accepts new code/IPC, fast-forwards durable762
to the exact candidate while preserving venv, then performs ONE native restart
with the unchanged source/credential configuration. It makes ONE MT canary
adoption report replying to seq7, and reads its AppendStore status. It does NOT
execute seq7 prose. After ChatGPT verifies that report in real OAuth history,
ChatGPT supplies a NEW explicit pure task before a bounded worker step. No
worker live-success/full-beta/global-fencing/delete claim before actual results.

## P24 owner panel through the existing MT gateway

P23b live pure worker result is confirmed at seq10/cbb52e9c7dfd30e9c70da8d54c1c44800a48c4a7.
Exit124 came from the operator's outer timeout120, not the Python error code.
Future outer whole-process limits use150s, inner response120 remains unchanged;
no rerun of the consumed task/report. Actual elapsed time remains unknown.

The existing PanelApplication remains read-only by default. Explicit runner
mode gateway binds the existing owner verifier to GatewayPanel, which uses
ONLY authority-mt.sock for both views and reports. No PAT, Git writer, queue
SQL, worker execution, lifecycle endpoints or request-selected actor/task/path.
The UI labels owner messages honestly as fixed MT reports to ChatGPT and only
offers a send form on MT's turn. It does not impersonate the OAuth CG process.

Each POST requires owner authentication, loopback/Host, exact same Origin,
strict framing, bounded form fields and an expiring server-held CSRF token.
Consume-before-IPC prevents duplicate clicks from sending again. An unknown
response locks further forms in that process and displays the exact ID for
history review, not an automatic retry button. The message ID is SHA256 of a
domain separator plus exact expected head/body, stable across panel restart;
same intent cannot acquire a fresh random publication ID. This is not a
durable browser-session journal or universal exactly-once claim. Restart loses
CSRF forms; old forms fail closed. Explicit new intent on a later turn is not
an automatic resend of an uncertain earlier one. All displayed data is escaped,
no scripts/cookies/passwords/tokens are forwarded to the gateway.

For staged live testing, gateway mode is restricted to127.0.0.1:8791, separate
from protected8790/8789/8787/native8765. It reads the already initialized panel
verifier normally, never resets/provisions a password. It creates only its NEW
private panel-gateway.p24.lock, separate from the live8790 server.lock; existing
lock/stale inode rejects startup. Normal cleanup removes only its owned inode.
No wakeup enable/key load/PAT prompt, native restart, unit or source change.
Serving retains StrictChannel; channel wait150 accommodates gateway reply120.

Standalone panel_gateway_acceptance --self-test exercises NEW real HTTP owner
auth/form/CSRF/duplicate denial through real Unix peers and the temporary native
host's SAME AppendStore. Only own children/temp SQLite/sockets/inmemory Git;
no real owner password/PAT/Git network/live services. It is separate from pytest
and must be run on the original operator environment, not by overriding fixtures
or retrying local OS-denied AF_UNIX. Live launch is allowed only after this new
acceptance succeeds. Merely passing offline tests does not prove live UI ready.
# P26: Git-reference event delivery (explicit native opt-in)

P26b corrects the installer's class-name guard: the actual pinned native class
is `Store`, not the earlier report's informal `BridgeStore` label. Both call
receivers are `self.store`; the existing lifespan call has only app, bridge and
dialog_factory, as expected before adding notification_store. Runtime/outbox,
credentials, native source pin and service policy are unchanged by this fix.
Guard failures now return a fixed structural guard name without source or
runtime values. Regression tests include the exact reported public shapes.

`git_event_install check|install|rollback` pins the complete native cf7 source.
It statically derives the shared public store receiver from `emit_test_event`
and changes only the existing lifespan call by adding `notification_store`.
It never imports the native module, follows helpers, reads subscriptions or
credentials, starts services, or changes existing unit policy. Install uses a
new private cf7 backup and same-directory 644 stage; errors retain partial facts.
Rollback archives only this new installed source and restores cf7, retaining
the newest durable code and all journals. It is not an automatic rollback.

When opted in, the native host owns a new private
`authority-events.p26.sqlite`. First creation requires exact reviewed Git head
dda52cff4167859d47d560dec7d7f0f080cb431a / sequence 12. Existing foreign/empty
files, altered checkpoints, aliases and unsafe metadata are rejected, not
adopted/reset. Existing AppendStore schema/data are unchanged. Historical
sequences through 12 never emit these notifications.

The first successful existing OAuth owner guard on `dialog_view`/`dialog_append`
durably binds a domain-separated principal hash on the gateway SQLite thread
before IPC. A different principal fails closed; no body/event selects owner,
task, socket, store or callback. The principal itself is not logged or stored.
Unsupported guard return types fail closed rather than using an unfiltered
subscription broadcast. Reopening retains the binding; no credentials are added.

Confirmed MT publication records after the checkpoint become metadata-only
outbox entries. An attempted-but-unconfirmed saved publication is recovered by
exact-message Git GET reconciliation only, never another append/object/ref write,
worker execution, new ID or recomputation. New local changes trigger a scan;
bounded pages and 30-second reconciliation cover crash gaps and temporary
failures. Failure in notification processing leaves committed dialogue intact.

Native `active_subscriptions()` / `enqueue_delivery(sub_id,event_id,payload)`
execute on the original asyncio loop. The active subscription's **owner hash,
event name and decoded arguments.queue** all must match. No callback URL,
signing secret, runtime owner ID or conversation body enters our outbox. The
payload contains exactly source=git-dialog, queue=test, task_id, head,
message_id, seq, from=mt, to=chatgpt. No job_id is invented; no conversation
Queue job, direct SQL delivery insert or forwarder.offer call is used.

Event ID is stable from the canonical metadata. Native per-subscription/event
uniqueness deduplicates an exact transport replay after crash-before-local-ack.
An acknowledgement here proves only native enqueue, NOT remote delivery/read,
execution, external exactly-once, credential-holder fencing or future subscriber
replay. Original native delivery retry/signing and expiry behavior remain in
charge. Shared native connection policy outside these calls is unchanged.

The existing automation must accept both legacy operational events and new
Git hints before activation. A Git hint is untrusted metadata: validate its
fixed scope and read the actual message through OAuth `dialog_view` at seq-1;
verify exact ID/seq/from/to and use the verified current Git head/turn, never
the event as content authority. A stale hint head need not equal today's head.
Git events have no queue job to claim or put_reply; legacy events retain normal
Work claim rules. Preserve the trigger and old operational transport/history
until an actual Git-event wake-up and separately reviewed migration succeed.

`python -m git_dialog.git_event_acceptance --self-test` is a NEW standalone
temp-only proof with real Unix peers, native lifespan thread, original asyncio
loop, private SQLite and the reported native uniqueness contract. Its OAuth and
Git are isolated fakes and its delivery store is a public-contract fixture,
**not** a live BridgeStore import or live end-to-end proof. Pytest remains
offline with all original socket/subprocess/temporary-SQLite guards intact.

