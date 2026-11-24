#!/usr/bin/env python3
"""Bounded protocol-level preflight of observer before the tunnel reroute.
No Postgres writes; no user data, MCP bodies, key, response headers printed.
"""
from stream35s import rpc, delete_session

def run_probe(*,port:int,secret:str)->bool:
    import stream35s as client
    assert port == 18779 and len(secret)==64
    client.PORT=port
    protocol='2025-06-18'
    sess=None
    try:
        initial,meta=rpc('initialize',{'protocolVersion':protocol,'capabilities':{},
                        'clientInfo':{'name':'35s-proxy-preflight','version':'1'}},
                         secret=secret,request_id=701)
        if not isinstance(initial.get('protocolVersion'),str):raise ValueError('BAD_PROTOCOL')
        protocol=initial['protocolVersion'];sess=meta['session']
        rpc('notifications/initialized',secret=secret,session=sess,protocol=protocol,notification=True)
        tools,_=rpc('tools/list',secret=secret,session=sess,protocol=protocol,request_id=702)
        expected={'managed_health','managed_create_status','managed_list','managed_status',
                  'managed_read','managed_send_status','managed_delivery_status','managed_create',
                  'managed_set_dispatch','managed_send_task','managed_ack_report'}
        names={v.get('name') for v in tools.get('tools',[]) if isinstance(v,dict)}
        if names!=expected:raise ValueError('BAD_TOOLSET')
        print('STAGE35S_OBSERVER_PREFLIGHT_INITIALIZE_11_TOOLS_OK_NO_DB_WRITES',flush=True)
        return True
    except Exception as e:
        # Never leak exception messages from remote service; only exception class.
        print('STAGE35S_OBSERVER_PREFLIGHT_FAILED_'+type(e).__name__,flush=True)
        return False
    finally:
        if sess:
            try:
                delete_session(secret=secret,session=sess,protocol=protocol)
            except Exception:
                print('STAGE35S_OBSERVER_PREFLIGHT_DELETE_NONFATAL',flush=True)
