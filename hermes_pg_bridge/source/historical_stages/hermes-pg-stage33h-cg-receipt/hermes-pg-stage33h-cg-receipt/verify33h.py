#!/usr/bin/env python3
"""Strict JSON receipt validator; never outputs message bodies or tokens."""
import json
import sys

class Stop(Exception): pass

def validate(d, phase):
    if not isinstance(d,dict):raise Stop('INVALID_JSON_OBJECT')
    for k in ('database_ok','canary_meta_ok','six_messages','seq5_exact','seq6_exact','seq6_lease_null'):
        if d.get(k) is not True:raise Stop('FAILED_'+k.upper())
    if d.get('outbox_count')!=6:raise Stop('OUTBOX_COUNT_CHANGED')
    if d.get('seq5_status')!='delivered' or d.get('seq5_attempts')!=1:raise Stop('SEQ5_NOT_DELIVERED_ONCE')
    if d.get('seq6_recipient')!='chatgpt':raise Stop('SEQ6_RECIPIENT_WRONG')
    if phase=='pre':
        if (d.get('seq6_status'),d.get('seq6_attempts'),d.get('seq6_delivered_at_set'))!=('pending',0,False):
            raise Stop('SEQ6_PRECONDITION_CHANGED')
        return 'STAGE33H_SEQ6_PENDING_EXACT_REPORT_PRECHECK_OK'
    if phase=='post':
        if (d.get('seq6_status'),d.get('seq6_attempts'),d.get('seq6_delivered_at_set'))!=('delivered',1,True):
            raise Stop('SEQ6_NOT_DELIVERED_EXACTLY_ONCE')
        return 'STAGE33H_SEQ6_DELIVERED_AND_SEQ5_UNCHANGED'
    raise Stop('INVALID_PHASE')

def main():
    try:
        if len(sys.argv)!=2:raise Stop('ARGUMENTS')
        lines=[s for s in sys.stdin.read().splitlines() if s.strip().startswith('{')]
        if len(lines)!=1:raise Stop('UNEXPECTED_QUERY_OUTPUT')
        print(validate(json.loads(lines[0]),sys.argv[1]))
    except (Stop,ValueError,TypeError) as e:
        print('STAGE33H_STOP_'+str(e).replace(' ','_'),file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
