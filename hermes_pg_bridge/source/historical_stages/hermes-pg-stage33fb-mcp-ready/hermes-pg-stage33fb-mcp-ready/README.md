# Hermes PG v2 — Stage 33F-B: role-bound fixed MCP plus process inventory

Stage 33F-A: new MT Inbox plugin candidate exists but is NOT registered. The
old active canary plugin is the only currently configured dispatcher. Several
MultiTool processes may have loaded plugin code; **never** run both plugins for
one `task_id`.

This bundle creates **only**:

1. A temporary role-bound test MCP on `127.0.0.1:18773/mcp`, using real
   PostgreSQL login `bridge_chatgpt` via Stage 33C actor adapter. Three tools:
   `stage33f_actor_health`, `stage33f_canary_status`, `stage33f_send_once`.
   **WRITE IS NOT ARMED in 33F-B**: the tool refuses unless a private 33F-C
   arm file is created after verifying exclusive MT dispatcher ownership.
   Once armed, it sends one immutable harmless canary with `seq=5` and fixed
   `message_id`/body. **Do not call this tool until a future coordinated cutover**.
2. A **private candidate config file** that would replace exactly one legacy
   canary plugin entry with the Stage 33F-A candidate. It DOES NOT change
   `~/.config/gigatool/opencode.json` or restart MultiTool.
3. A private copy of the original config for a future verified rollback.
4. A safe snapshot of MultiTool/Node process identity (PID, PPID, process group,
   session, start-ticks, basename only). NEVER prints command lines, environment,
   DB passwords, API keys, SSH endpoints or message bodies.

The Stage 33F-B server is **LOOPBACK ONLY**, no OAuth. Its fixed and harmless
write tool is **not production authorization**. Never tunnel it before
exclusive ownership of the canary dispatcher is proven. Do not enable it on
boot. Does not attach OpenAI Secure Tunnel; current tunnel stays on Stage 31.

This phase performs NO DB writes, NO SDK calls, NO MT config edits, NO service
restarts. It only starts its new isolated loopback user service. Stage 33F-C
will be the coordinated handover and real SDK test after examining the
runtime process inventory and explicit owner approval.

If the Stage 33F-A config SHA or candidate SHA changes, abort. If canary
is not seq 4/ChatGPT turn, abort. This is intentionally strict.

Run as `kali` in this folder:

```
sha256sum -c SHA256SUMS
python3 -m py_compile logic33fb.py cg_mcp33fb.py prepare_cutover33fb.py inventory33fb.py test_stage33fb.py
python3 -m unittest -v test_stage33fb
for f in *.sh; do bash -n "$f"; done
bash 39_preflight.sh
bash 39_install_mcp_only.sh
bash 39_verify_only.sh
```

Report complete JSON inventory and markers. Do NOT send `stage33f_send_once`.
If installation fails, do not run installer again. The read-only verifier is
safe to re-run after debugging. `39_disable_mcp_only.sh` stops only the new
loopback MCP and does not touch any current bridge service.
