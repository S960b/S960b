# Explicit pinned bridge bootstrap

This closes the startup gap: original main still builds its application, but
the wrapper calls `enable_beta_wakeup` on that application BEFORE it is returned
to the server/lifespan. Original delivery_loop, OAuth/subscriptions/DB routing
and original main server settings are not replaced.

Default check, no import/execution/keys/state read:

```
PYTHONPATH=src python -m git_dialog.bridge_bootstrap --source VERIFIED_CANDIDATE --context-source KNOWN_ORIGINAL_BRIDGE_SOURCE
```

Requires candidateSHA30e6307ed09313fcf21fa947380214e5adbb9d2b35442ed88d728c95edbbd11f
and originalSHAffbbe5b4252f16544ed9de09f4a715cff2cb4347199866c47ba4584eb726647f.
Regular UID-owned, not group/world-writable files; NOFOLLOW,512KiB cap,
AST parse only. Changed original means stop, not rebase or overwrite.

Explicit future run adds `--run --state-dir NEW_CANDIDATE_STATE`. It validates
the existing separate owner DB and dedicated key first. No provisioning,
credential discovery, PAT/owner password read/echo, service stop, port takeover
or systemd/config mutation. It must run with the original bridge's existing
configured runtime/interpreter/environment and working directory plus this
package on PYTHONPATH. It does not install missing bridge dependencies.

Run executes ONLY the verified local application code bytes (not a Git/job
body), in a private module name, with the original __file__, sibling import
directory and original argv context. This preserves existing path-based
settings instead of accidentally using the candidate temporary directory as
live state. Dependencies are those trusted existing local bridge modules;
their hashes are not pinned by this tool, so it is not a full runtime audit.
Original configured bridge code may normally access its DB/OAuth as before.
The wrapper supplies no new public route or message-selected code execution.

Default check is NOT runtime integration acceptance. Offline fixture tests
verify argument preservation/enable-before-return/default-deny/lock release;
they do not prove actual bridge import compatibility or startup. Enabling a
second server on an occupied bridge port will fail; this tool never kills the
existing server. A targeted, reversible launcher/service handoff with preserved
environment, source backup and rollback plan remains a distinct deployment
operation after actual check/acceptance. P1/8787/8789 are not replaced here.
Live Git and writer authority must not be inferred from source_checked JSON.

## Failed P8 startup: bounded diagnosis

The actual original main is synchronous and blocks on asyncio.run/uvicorn.
The failed P8 service emitted only bootstrap_failed into its already-configured
append sink, not the journal. Its exception stage/type remain UNKNOWN. The
original service was restored by the reviewed rollback; no live retry is implied.

An explicit --validate-state (mutually exclusive with --run) requires state-dir,
checks the two pinned code files and validates the existing separate owner DB
and dedicated key exactly as run would, then exits. It does NOT execute/import
the original bridge, build an ASGI app, take a pilot lock, open a listener or
network, read PAT/password/config/environment, or mutate services. It necessarily
reads the existing local owner verifier and key through reviewed loaders, never
outputs their values/path/fingerprint, and never provisions/rotates/repairs them.
Success reports prerequisites_checked/state_validated/key_validated/bridge_started
false. This is not proof that the service's inherited runtime environment is the
same as the operator's console, nor proof that module execution/startup will work.

Bootstrap errors now carry ONLY fixed allowlisted phase/type/code tags, never an
exception message, traceback, source/config line, credential path, key or body.
Execution boundaries distinguish module_execute/configure/build_app/enable_wakeup/
enable_cleanup/main/result. Unknown exception classes/codes map to generic tags.
Default check retains its no-state/no-key behavior. Guards are not weakened and
no new source/candidate generation or live restart is automatic.
