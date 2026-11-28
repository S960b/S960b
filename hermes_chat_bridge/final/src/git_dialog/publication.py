"""Publish ONLY an already durable report. No task execution or callbacks.

The one-time send grant is committed BEFORE any remote write. After a crash or
ambiguous send, recovery observes Git; missing result never authorizes a resend.
"""
import json

from . import DialogError, parse_message, prepare_append


def _confirm(journal, task_id, message_id, run_id, commit_sha, saved_result):
    try:
        return journal.mark_published(task_id, message_id, run_id, commit_sha)
    except DialogError as exc:
        if exc.code != "publication_conflict":
            raise
        # Another observer may have committed confirmation using an earlier
        # reachable snapshot. Keep its immutable first confirmation, not ours.
        row = journal.get(task_id, message_id)
        if (row["status"] != "published" or row["run_id"] != run_id or
                row["result"] != saved_result):
            raise
        return row


def publish_saved(journal, dialog, task_id, message_id, run_id):
    row = journal.get(task_id, message_id)
    if row["run_id"] != run_id:
        raise DialogError("not_execution_owner")
    if row["status"] not in ("result_ready", "published") or not row["result"]:
        raise DialogError("result_not_ready")
    incoming = parse_message(json.loads(row["payload"]))
    report = parse_message(json.loads(row["result"]))
    snap = dialog.load(task_id)
    actual_input = next((m for m in snap.messages if m.message_id == message_id), None)
    if actual_input != incoming:
        raise DialogError("remote_input_mismatch")
    existing = next((m for m in snap.messages if m.message_id == report.message_id), None)
    if existing is not None:
        if existing != report:
            raise DialogError("result_conflict")
        # Snapshot has been fully validated by the adapter; this exact saved
        # report is reachable via the observed ref. Never regenerate a result.
        observed_head = snap.head
        confirmed = row["commit_sha"] if row["status"] == "published" else observed_head
        return _confirm(journal, task_id, message_id, run_id, confirmed, row["result"])
    if row["status"] == "published":
        raise DialogError("published_report_missing")
    if row["publication_attempted"]:
        raise DialogError("publication_unresolved")
    prepared = prepare_append(snap, snap.head, task_id=task_id,
                              message_id=report.message_id, actor=report.sender,
                              kind=report.kind, body=report.body,
                              created_at_utc=report.created_at_utc)
    if not prepared.created or prepared.message != report:
        raise DialogError("result_does_not_match_remote_turn")
    if not journal.reserve_publication(task_id, message_id, run_id):
        raise DialogError("publication_unresolved")
    # No exception handler retries this call. The reservation survives failures.
    dialog.append(task_id, snap.head, message_id=report.message_id,
                  actor=report.sender, kind=report.kind, body=report.body,
                  created_at_utc=report.created_at_utc)
    # Re-read, do not trust a returned receipt or ACK by itself.
    receipt = dialog.reconcile(task_id, report)
    return _confirm(journal, task_id, message_id, run_id, receipt.commit_sha, row["result"])
