"""Public, redacted P31 error codes for the trusted Unix authority boundary.

Never pass exception.detail, Git response, paths, tokens or tracebacks across
the socket. These code names are a deliberately small allowlist.
"""
from . import DialogError

PUBLIC_CODES = frozenset({
    "wrong_turn", "head_mismatch", "peer_denied", "timeout",
    "invalid_arguments", "not_found", "conflict", "authority_unavailable",
    "rate_limited",
})

_CODE_MAP = {
    "wrong_turn": "wrong_turn",
    "wrong_actor": "wrong_turn",
    "stale_head": "head_mismatch",
    "authority_peer_denied": "peer_denied",
    "authority_writer_denied": "peer_denied",
    "gateway_actor_denied": "peer_denied",
    "gateway_task_denied": "peer_denied",
    "catalog_request_rejected": "invalid_arguments",
    "authority_request_rejected": "invalid_arguments",
    "invalid_task_id": "invalid_arguments",
    "invalid_message_id": "invalid_arguments",
    "invalid_dialog_page": "invalid_arguments",
    "invalid_body": "invalid_arguments",
    "body_too_large": "invalid_arguments",
    "github_rate_limited": "rate_limited",
    "invalid_dialog_title": "invalid_arguments",
    "unregistered_dialog": "not_found",
    "creation_not_found": "not_found",
    "append_not_found": "not_found",
    "creation_request_conflict": "conflict",
    "message_id_conflict": "conflict",
    "dialog_registration_conflict": "conflict",
}


def public_code(exc):
    """Only explicit known protocol failures are eligible for public disclosure."""
    if not isinstance(exc, DialogError):
        return None
    return _CODE_MAP.get(exc.code)


def is_public(code):
    return type(code) is str and code in PUBLIC_CODES


def error_value(code):
    """Tool payload after failed IPC: no implicit write retry is authorized."""
    return {"ok": False, "code": code if is_public(code) else "authority_unavailable",
            "retry_authorized": False}
