"""Fixed MT writer capabilities over the existing private Unix gateway only.

stdin JSON, no PAT/Git/SQLite/Queue/fallback, no body-selected actor/task/path.
One call; timeout is UNKNOWN, not cancellation or retry authority.
"""
import json
import sys

from . import DialogError
from .github import decode_json
from .unix_authority import SOCKETS, UnixClient

FIELDS = {
    'canary': ('canary_report', {'head', 'message_id', 'body'}),
    'worker': ('worker_step', {'request_id', 'report_id', 'run_id'}),
    'status': ('writer_status', {'message_id'}),
}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 1 or args[0] not in FIELDS:
            raise DialogError('authority_writer_options_rejected')
        raw = sys.stdin.buffer.read(8193)
        if len(raw) > 8192:
            raise DialogError('authority_request_rejected')
        obj = decode_json(raw.decode('utf-8'))
        op, fields = FIELDS[args[0]]
        if type(obj) is not dict or set(obj) != fields:
            raise DialogError('authority_request_rejected')
        result = UnixClient(SOCKETS['mt']).call({'op': op, **obj})
    except Exception:
        result = {'error': 'authority_result_unconfirmed', 'retry_authorized': False}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return int('error' in result)


if __name__ == '__main__':
    raise SystemExit(main())
