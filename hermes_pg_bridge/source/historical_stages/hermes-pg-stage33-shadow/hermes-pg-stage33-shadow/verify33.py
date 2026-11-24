#!/usr/bin/env python3
"""Read-only verifications with strictly bounded, secret-free output."""
from __future__ import annotations
import json
import sys

EXPECTED_TRUE = ('role_chatgpt_no_login','role_agent_no_login','legacy_execute',
                 'chatgpt_send','agent_send','agent_claim')
EXPECTED_FALSE = ('chatgpt_raw_send','agent_raw_send','agent_as_chatgpt',
                  'chatgpt_as_agent','chatgpt_table_select','agent_table_select',
                  'chatgpt_table_insert','agent_table_insert','chatgpt_db_create',
                  'agent_db_create','chatgpt_schema_create','agent_schema_create')
EXPECTED_ZERO = ('public_exec_count','wrong_wrapper_owner_count','unprotected_wrapper_count')


def validate(blob: str) -> dict:
    data = json.loads(blob)
    if not isinstance(data, dict) or data.get('database') != 'bridge':
        raise ValueError('unexpected_database')
    if data.get('function_count') != 25:
        raise ValueError('unexpected_function_count')
    for key in EXPECTED_TRUE:
        if data.get(key) is not True:
            raise ValueError('expected_true_'+key)
    for key in EXPECTED_FALSE:
        if data.get(key) is not False:
            raise ValueError('expected_false_'+key)
    for key in EXPECTED_ZERO:
        if data.get(key) != 0:
            raise ValueError('expected_zero_'+key)
    if not (isinstance(data.get('dialog_count'), int) and isinstance(data.get('message_count'),int)):
        raise ValueError('count_missing')
    return data


def main():
    try:
        raw=sys.stdin.read(65536)
        obj=validate(raw.strip())
    except Exception as e:
        print('STAGE33_STOP_VERIFY_'+type(e).__name__,file=sys.stderr)
        return 2
    print('STAGE33_SHADOW_ROLE_GRANTS_VERIFIED')
    print('STAGE33_CROSS_ACTOR_EXECUTE_DENIED')
    print('STAGE33_NOLOGIN_AND_NO_TABLE_PRIVILEGES')
    print('STAGE33_LEGACY_ACCESS_STILL_AVAILABLE')
    print('STAGE33_DB_COUNTS_BEFORE_AFTER_NOT_COMPARED')
    return 0

if __name__=='__main__':
    sys.exit(main())
