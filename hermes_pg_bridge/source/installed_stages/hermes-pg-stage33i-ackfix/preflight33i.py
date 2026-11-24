#!/usr/bin/env python3
"""Read-only health/receipts preflight. No mutations or SDK calls."""
import sys
from pathlib import Path
from build33i import preflight,CANARY

def main():
    preflight()
    sys.path.insert(0,'/home/kali/hermes-pg-stage33e-shadow-clients')
    from actor_import33e import actor_classes
    C,A=actor_classes()
    for cls in (C,A):
        a=cls()
        if a.health().get('ok') is not True:
            raise RuntimeError('actor_login_unavailable')
        m=a.dialog_metadata(CANARY)
        if m.get('last_seq') !=6 or m.get('state')!='active':
            raise RuntimeError('canary_turn_changed')
        for seq in (5,6):
            d=a.delivery_status(CANARY,seq)
            if not isinstance(d,dict) or d.get('status')!='delivered':
                raise RuntimeError('canary_delivery_unresolved')
    print('STAGE33I_STRICT_PRECHECK_BOTH_RECEIPTS_DELIVERED')
    print('STAGE33I_REAL_ACTOR_DB_LOGINS_AVAILABLE')
    print('STAGE33I_CURRENT_PLUGIN_AND_CONFIG_HASH_PINNED')
    print('STAGE33I_NO_DATABASE_WRITES_PREFLIGHT')

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('STAGE33I_PREFLIGHT_STOP_'+type(e).__name__+'_'+str(e).splitlines()[0][:80])
        raise SystemExit(2)
