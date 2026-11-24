#!/usr/bin/env python3
"""Strictly verify only sanitized owner-role read-only receipt JSON."""
import json
import sys

class VerifyError(Exception):
    pass

REQUIRED = (
    'database_exact', 'admin_identity_exact', 'dialog_exact',
    'message_count_exact', 'task_exact', 'report_exact',
    'seq5_valid_lease_or_delivered', 'seq6_pending',
)

def verify(text, *, after=False):
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if len(lines) != 1:
        raise VerifyError('receipt_output_shape_invalid')
    try:
        data = json.loads(lines[0])
    except (ValueError, TypeError):
        raise VerifyError('receipt_json_invalid') from None
    if not isinstance(data, dict) or data.get('stage') != '33G':
        raise VerifyError('receipt_stage_invalid')
    if not all(data.get(k) is True for k in REQUIRED):
        raise VerifyError('receipt_invariant_failed')
    status = data.get('seq5_status')
    if status not in ('leased','delivered'):
        raise VerifyError('receipt_delivery_state_invalid')
    if after and status != 'delivered':
        raise VerifyError('receipt_delivery_not_closed')
    return status

if __name__ == '__main__':
    try:
        stat = verify(sys.stdin.read(),after=(len(sys.argv)==2 and sys.argv[1]=='post'))
    except VerifyError as e:
        print('STAGE33G_STOP_'+str(e).upper(),file=sys.stderr)
        raise SystemExit(2)
    print('STAGE33G_READ_ONLY_STRICT_RECEIPTS_VERIFIED')
    print('STAGE33G_SEQ5_STATUS_'+stat.upper())
    if len(sys.argv)==2 and sys.argv[1]=='post':
        print('STAGE33G_SEQ5_DELIVERED_SEQ6_UNCHANGED')
