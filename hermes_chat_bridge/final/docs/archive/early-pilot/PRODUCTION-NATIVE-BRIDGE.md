# Preserve the native bridge startup

P9 reached original build_app and raised RuntimeError before enable_wakeup.
Reviewed source shows an OWNER_PASS=os.environ.get('BRIDGE_PASS') guard followed
by pilot_database_lock, which also has RuntimeError paths. The specific guard
that fired is NOT established by that phase tag alone. The old startup wrapper
and actual environment were not inspected. Never extract/copy BRIDGE_PASS from
a running process, unit, config, log or history, or remove the auth/pilot guards.

The launcher override is not a proven replacement for the full native startup.
This candidate approach instead keeps original ExecStart, interpreter, CWD,
environment preparation, __name__, original main and source path intact.

Explicit candidate-only generation:

```
PYTHONPATH=src python -m git_dialog.bridge_patch --source KNOWN_ORIGINAL --output NEW_NATIVE_CANDIDATE --native-entry
```

It still requires original SHA256ffbbe5b4252f16544ed9de09f4a715cff2cb4347199866c47ba4584eb726647f,
AST/unique anchors and a NEW O_EXCL600 output. It never imports/executes original
source, changes the live file, creates activation markers/keys, or touches a
service/unit. The prior default P6 transformation remains unchanged.

Native mode preserves the four reviewed P6 changes, but temporarily adds the
fixed /home/kali/agent-dialog-beta/code/src to sys.path ONLY on explicit enable,
restoring sys.path in finally. Dependencies remain trusted local code, not a
sandbox. No environment/PYTHONPATH changes, arbitrary paths or message callbacks.

It adds configure_beta_wakeup() after the existing app.state.bridge assignment,
before lifespan. Missing fixed /home/kali/agent-dialog-beta/state/bridge-wakeup.enabled
means OFF: no package import/key read/forwarder/thread/network, original startup
continues. An existing marker must be regular, NOFOLLOW, current UID,600,0bytes,
singlelink; malformed markers fail closed. Activation then calls the reviewed
key loader, which additionally verifies private state directory and marker/key
permissions. This is a separate nonsecret activation marker, not the HMAC key.
Enable failure closes original pilot_lock before propagating; lifespan still
owns forwarder start/stop and original lock cleanup. Auth, DB pilot lock,
producer metadata/delivery and original main are not removed/replaced.

No activation/install/restart is performed by the generation tool. The actual
native working-source candidate must first be generated, hashed, syntax-checked,
and reviewed with all eight existing dirty hunks retained. A later distinct
reviewed deployment would preserve a byte-exact original-code backup, install
only the hash-pinned reviewed candidate with explicit local activation, and
restart ONLY hermes-bridge once. This source change is specifically within the
beta bridge, not permission to overwrite unrelated dirty work. Never install
the old Git HEAD over the working source or overwrite old backup/candidate files.

deploy/91-agent-dialog-beta-native.conf changes ONLY Restart=no for first
acceptance. It deliberately has NO ExecStart, environment, CWD, credential or
output-sink directive. It is a future explicit scoped operational artifact,
not installed by this code. Old ExecStart-reset drop-ins must remain inactive.
Rollback must restore original source bytes and native startup, move only our
new activation/policy artifacts to new recoverable backup names, and preserve
DBs/keys/history/old rollback files and other services. Exact deployment and
rollback paths/guards require the actual candidate hash and current metadata
before any live instruction is issued. Candidate/default-off tests are not
evidence of actual credential preparation or successful live forwarding.

## P11 fixed-target installer and recovery

MT generated the actual native candidate with SHA256
4921809e5aa9e7a67f7bee77f355d58123b076507d9d8f54da6b67dd4451c585,
1279 lines, regular600/currentUID/singlelink, syntax-check exit0; original
ffbbe5b4252f16544ed9de09f4a715cff2cb4347199866c47ba4584eb726647f remained
unchanged with eight dirty hunks. These are operator reports, not an independent
deployed audit. Missing activation marker means OFF, not activation failure.

`python -m git_dialog.native_install check --candidate REVIEWED_NATIVE_FILE`
reads only the pinned code files and directory metadata/absence guards. It
does not read state contents/key/verifier, write files, import/execute the
bridge or start/restart a service. All target paths and both SHA256 values
are fixed in code; --candidate is only a nonsensitive code-file input.

`install --candidate REVIEWED_NATIVE_FILE` creates NEW
`/home/kali/agent-dialog-beta/bridge-original.p11.py`600 containing exact original
bytes and NEW source-sibling `.bridge-server.p11-stage`644. Each is O_EXCL,
NOFOLLOW, fsynced and hash-verified. fchmod applies ONLY to a newly created inode
before publication, not repair of any pre-existing file. It rechecks original
identity/hash then atomically replaces ONLY the fixed source and fsyncs its
directory. Source ends644, backup remains600; no activation or service action.
Old P6 candidate and P8/P9 rollback files are never touched.

Explicit future `activate` requires installed native hash and original backup,
validates existing AuthAuthority and dedicated key through ready local loaders,
then creates ONLY the NEW zero-byte600 fixed activation marker. No values,
credential paths/fingerprints, exception text or config are emitted. No key
provisioning, password/PAT prompt or service action. This action requires its
own reviewed operational stage; installing does not implicitly authorize it.

`deactivate` verifies the installed hash, backup and our empty/private marker,
retains the marker under NEW `bridge-wakeup.p11-disabled` using no-clobber
link/unlink and fsync. It never overwrites a prior disabled artifact. A crash
between link/unlink leaves retained artifacts and requires review, not retry.
This changes startup opt-in only, not the already running forwarder.

`rollback` requires no active marker, verifies installed native and original
backup, creates NEW `bridge-native.p11-rollback.py`600 archive and source-sibling
`.bridge-server.p11-restore`644 with exact original bytes, verifies/rechecks then
atomically restores fixed source644. It retains backup, native archive and any
disabled marker. Operator must separately restore ONLY our Restart=no policy
and restart ONLY hermes-bridge.service once under a reviewed packet. Original
unit contents/ExecStart/environment are never read or changed by this module.

Run actions serially, with no concurrent source/artifact writers. Identity/hash
rechecks plus atomic rename are NOT universal CAS or a hostile-writer sandbox.
No automatic retries, repairs, cleanup or rollback on tool errors: partial NEW
artifacts remain and safe JSON reports the last phase. An error after replace
can mean source changed despite a failed final verification; do not infer no
mutation from exit1. Never improvise around a guard failure. The installer
does not query systemd, network, logs, DB history, environment or credentials.

First reviewed native acceptance should keep activation absent, preserve the
original native ExecStart and prove stable startup before a later opt-in stage.
Candidate creation, installer tests and service startup alone are not proof of
live wakeup delivery or common write authority. Delete remains disabled.
