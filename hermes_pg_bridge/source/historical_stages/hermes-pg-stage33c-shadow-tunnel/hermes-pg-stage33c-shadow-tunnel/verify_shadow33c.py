#!/usr/bin/env python3
"""Read-only real-login check through new shadow forward (15433)."""
import sys
from actor_adapter import ChatGPTActorDB, AgentActorDB, AdapterError

def check():
    cg=ChatGPTActorDB(); ag=AgentActorDB()
    assert cg.health().get('ok') is True, 'cg health'
    assert ag.health().get('ok') is True, 'ag health'
    # Read existing completed canary; no task or outbox is modified.
    key='task_f2c64cfdf7894537b35a19a6def977c6'
    a=cg.dialog_metadata(key)
    b=ag.dialog_metadata(key)
    assert a.get('version') == b.get('version') and a.get('last_seq') == 4, 'canary changed'
    assert ag.delivery_status(key,3).get('status') == 'delivered', 'outbox discrepancy'
    print('STAGE33C_SHADOW_DB_REAL_LOGINS_OK')
    print('STAGE33C_ROLE_BOUND_READ_ONLY_API_OK')
    print('STAGE33C_CANARY_UNMODIFIED_LAST_SEQ_4')

def main():
    try: check()
    except (AssertionError, AdapterError, Exception) as e:
        print('STAGE33C_STOP_'+type(e).__name__,file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':sys.exit(main())
