# Reviewed metadata adapter, not yet installed bridge wiring

MT provided the working-copy producer contract from
`S960b/S960b/research/hermes_chat_bridge/bridge_server.py`:
`BridgeApp.emit_test_event(job_id, queue=None)` builds six metadata fields and
calls `BridgeStore.enqueue_delivery`. The delivery envelope additionally has
`cursor:null`; the module has **8 uncommitted hunks**, so the cited Git commit
is NOT the deployed source. This is review of a reported fragment, not an
independent deployed-file audit. Never overwrite that file from Git HEAD.

`WakeupForwarder(key, port=8790)` is an opt-in, process-local producer adapter.
Construction has no thread/socket; explicit start/stop own one thread. Offer
accepts only the exact six metadata fields, scopes task/queue/direction/status
to the existing accepted protocol, and constructs the dedicated four-field
envelope. It never forwards job bodies, cursor or subscription information,
reads/claims jobs, changes leases/ACKs or writes Git. eventId uses the reported
producer's queue:job SHA256 convention. Timestamp is the adapter's fresh UTC;
created_at remains the original job timestamp. Gate still enforces HMAC and
fresh event age/future limit, but now permits older job creation (only future
creation > event+5s is rejected).

Adapter offer is nonblocking/best-effort, queue1, hash replay/rate gate
TTL180s/256/cooldown30, duplicate/coalescing hints may be dropped. The reader's
60s periodic polling remains fallback; this is not an execution/handoff ACK or
durable/exactly-once delivery. Errors do not escape offer into the producer.
The worker posts only to literal127.0.0.1/nonprivileged port, fixed
`/bridge/wakeup`, no proxy/env/DNS/redirect/Basic owner authority. A total2s
network deadline and bounded8192-byte response headers prevent trickling
responses from blocking indefinitely. No retry, secret/response/error output.
Stop discards pending hints and joins the own thread within3s.

Dedicated key provisioning/load is described in PRODUCTION-WAKEUP-KEY.md.
It does not reuse the existing Standard Webhooks signature or subscription
secret. This adapter is NOT registered as an existing webhook subscriber.

Proposed wiring, to be applied only after exact working-source review:
initialize/start one adapter at trusted bridge startup using explicit beta
candidate state/key and fixed local endpoint; default None/off. At the reported
producer point after metadata collection, call `forwarder.offer(payload)` if
configured; original subscription/enqueue logic remains untouched. On bridge
shutdown call stop. No hook has been applied to the dirty working bridge by
this commit; no subscription/service/P1/8787/8789 changes are deployed.

Once after offline tests, run the new hop's synthetic acceptance:

```
timeout 10s env PYTHONPATH=src python -m git_dialog.forward_smoke --self-test
```

This uses a new memory-only random test key and a temporary loopback port0
HTTP receiver validating the real gate, delayed synthetic metadata and actual
forwarder thread/network code; then joins/closes its own resources. No DB,
PAT, real job, bridge, Git, owner password or persistent key. It proves this
hop, not live source-hook wiring, complete HTTP-server hardening (the tiny
test HTTPServer is not the production runner), real Git shutdown or shared
writer authority. Those cannot be claimed from this synthetic test.
