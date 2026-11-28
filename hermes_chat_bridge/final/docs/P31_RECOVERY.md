# P31: failure diagnosis and safe stalled-dialog recovery

P30's turn invariant remains immutable: only `waiting_for` may publish the
next message. **Never mutate Git refs, state.json, seq, next_actor, SQLite
grants, the inbox cursor or session affinity to force a turn.** Never choose a
new message_id to retry an uncertain Git write.

## Read-only diagnosis

Call `dialog_view(task_id=...,offset=0,limit=2,order="newest")` using the
authenticated owner. View is bounded to a **256 KiB history page** and 100
messages maximum; use pagination, not an unbounded full-history query.
Read `head`, `waiting_for`, `last_message_at`,
`seconds_since_message`, `stalled`, `lifecycle`, latest `seq`, `to`.
`stalled=true` means the latest message is at least 600 seconds old and the
dialogue is nonterminal; **it does not prove that MT crashed**.

If `waiting_for=mt` and the newest message is `to=mt`:

1. Check whether the same message's `seq` / `message_id` was previously
   delivered to the existing session. Check its local worker state and errors.
2. Restart **only the MT inbox/worker** if failed; allow its normal
   read-only `list_dialogs -> view` scan to re-dispatch into that same
   pinned session. If its dedupe state already marks the message delivered
   but execution failed, recover it using an operator-approved **same-session,
   same-seq replay** from the existing trusted inbox facility, not a new chat.
3. If MT may have already published a reply, **read/reconcile** its stable
   message_id and the updated Git HEAD before trying any additional write.
   An ambiguous network timeout is NOT permission to retry with a new ID.
4. Only MT (the existing turn owner) may append a new report. ChatGPT cannot
   'unlock' the turn by writing as MT. If MT cannot resume, leave it stalled
   and escalate to the owner. Do not fake an MT ACK.

If `waiting_for=chatgpt`, the corresponding steps apply to the ChatGPT
subscriber/consumer; MT must not publish an extra report.

## Error contract

When the authority can identify a known protocol rejection, the MCP tool may
return `{"ok":false,"code":"wrong_turn","retry_authorized":false}`.
Other public codes include `head_mismatch`, `peer_denied`,
`invalid_arguments`, `not_found`, and `conflict`.
Unknown or transport failures return `authority_unavailable`. Neither
category authorizes replay of an unknown write effect. Keep the original
`request_id` / `message_id` for GET-only reconciliation.

## Live-gate checklist

- Run pytest on an **isolated** worktree; do not modify installed P30.
- Test native FastMCP registration, annotations, OAuth guard, and error mapping.
- Run full existing suite, then compare successful P30 receipts byte-for-byte.
- Only after green tests, prepare a reversible deployment and explicit rollback.
- Verify subscribed append notifications separately for each newly created
  dialogue (the existing historical notification pump is task-scoped).
