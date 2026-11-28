"""One MT canary report to ONE owner-authorized existing dialogue branch.

No create/delete-ref, execution journal, subprocess or message-body execution.
Historical offline library fixture only; credential-taking CLI is disabled.
Use authority_writers canary over the installed MT gateway instead.
"""
import json
import sys

from . import DialogError
from .github import ApiError, GitHubDialog, sha
from .preflight import BASE, MAIN_SHA, TASK_ID, JsonHttp, read_configured_token

FIRST_ID = "chatgpt_canary_v1"
FIRST_BODY = "GIT_DIALOG_CANARY_V1_CHATGPT_NO_EXECUTION"
REPORT_ID = "mt_canary_v1"
REPORT_BODY = "GIT_DIALOG_CANARY_V1_MT_NO_EXECUTION"


class ProbeTransport:
    """Ref creation/deletion/main mutations are impossible through this guard."""
    def __init__(self, transport):
        self.transport = transport

    def request(self, method, path, payload=None):
        allowed = method == "GET" and (path == BASE or path.startswith(BASE + "/git/"))
        if method == "POST":
            allowed = path in (BASE + "/git/trees", BASE + "/git/commits")
        if method == "PATCH":
            allowed = (path == BASE + "/git/refs/heads/dialogs/" + TASK_ID and
                       isinstance(payload, dict) and set(payload) == {"sha", "force"} and
                       payload["force"] is False)
        if not allowed:
            raise DialogError("live_probe_request_forbidden")
        return self.transport.request(method, path, payload)


def run_probe(transport, initial_head):
    sha(initial_head)
    guard = ProbeTransport(transport)
    def main_head():
        obj = guard.request("GET", BASE + "/git/ref/heads/main")
        if obj.get("ref") != "refs/heads/main" or obj.get("object", {}).get("sha") != MAIN_SHA:
            raise DialogError("main_checkpoint_changed")
    dialog = GitHubDialog(guard)
    snap = dialog.load(TASK_ID)
    main_head()
    if (len(snap.messages) not in (1, 2) or snap.messages[0].message_id != FIRST_ID or
            snap.messages[0].kind != "ack" or snap.messages[0].body != FIRST_BODY or
            snap.state.status != "working" or snap.state.revision != len(snap.messages)):
        raise DialogError("unexpected_pilot_snapshot")
    if len(snap.messages) == 2:
        report = snap.last
        if report.message_id != REPORT_ID or report.kind != "report" or report.body != REPORT_BODY:
            raise DialogError("unexpected_pilot_report")
        observed = dialog.reconcile(TASK_ID, report)
        created = False
    else:
        if snap.head != initial_head:
            raise DialogError("unexpected_initial_head")
        observed = dialog.append(TASK_ID, initial_head, message_id=REPORT_ID,
                                 actor="mt", kind="report", body=REPORT_BODY)
        created = observed.created
    main_head()
    return {"task_id": TASK_ID, "report_message_id": REPORT_ID,
            "confirmed_commit": observed.commit_sha, "seq": 2,
            "next_actor": "chatgpt", "created": created,
            "publication": "observed_exact_report", "main_sha": MAIN_SHA,
            "execution": "not_implemented_or_run"}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print('{"error":"expected_local_credential_file_and_initial_head"}')
        return 2
    try:
        initial_head = sha(args[1])
        raise DialogError('legacy_direct_writer_disabled_use_authority_writers')
    except ApiError as exc:
        result = {"error": "github_http_error", "status": exc.status}
    except DialogError as exc:
        result = {"error": exc.code}
    except Exception:
        result = {"error": "live_probe_failed"}
    print(json.dumps(result, sort_keys=True))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())

