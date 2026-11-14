#!/usr/bin/env python3
from ldap3 import Server, Connection, NTLM, ENCRYPT, SUBTREE
import struct
from impacket.ldap.ldaptypes import SR_SECURITY_DESCRIPTOR
PW="<PW>"
srv=Server("<DC02_IP>", port=389, use_ssl=False, connect_timeout=12)
conn=Connection(srv, user=r"CORE\svc_enrollment", password=PW, authentication=NTLM,
                session_security=ENCRYPT, auto_bind=True, auto_referrals=False, receive_timeout=25)
def sidstr(b):
    if not isinstance(b,bytes):
        if isinstance(b,str): b=b.encode('latin1').decode('unicode_escape').encode('latin1')
        else: b=bytes(b)
    rev=b[0];n=b[1];auth=b[2:8];au=0
    for x in auth: au=(au<<8)|x
    s="S-1-%d-%d"%(rev,au)
    for i in range(n): s+="-%d"%struct.unpack('<I',b[8+i*4:12+i*4])[0]
    return s
OUR={'S-1-5-21-2429661416-260090189-2427659033-1103','S-1-5-21-2429661416-260090189-2427659033-513','S-1-5-11','S-1-1-0','S-1-5-32-548'}
ACCESS={'GenericAll':0x10000000,'GenericWrite':0x40000000,'WriteDacl':0x40000,'WriteOwner':0x80000,'ControlAccess':0x100,'ResetPwd':0x40,'WritePwd':0x20,'WriteMembers':0x02}
for dn in ['CN=Administrator,CN=Users,DC=core,DC=corp','CN=svc_enrollment,CN=Users,DC=core,DC=corp']:
    conn.search(search_base=dn, search_filter='(objectClass=*)', search_scope=SUBTREE, attributes=['nTSecurityDescriptor','distinguishedName'])
    if not conn.entries: print(f"no entry {dn}"); continue
    sd_raw=conn.entries[0].nTSecurityDescriptor.value if hasattr(conn.entries[0],'nTSecurityDescriptor') else None
    if not sd_raw:
        # try via raw dict
        sd_raw=conn.entries[0].entry_attributes_as_dict.get('nTSecurityDescriptor',[None])[0]
    if not sd_raw: print(f"{dn}: no sd readable"); continue
    raw=sd_raw if isinstance(sd_raw,bytes) else sd_raw.encode('latin1')
    try:
        sd=SR_SECURITY_DESCRIPTOR(); sd.fromString(raw)
        print(f"\n=== DACL on {dn} ===")
        if sd['Dacl']:
            for ace in sd['Dacl']['Data']:
                try:
                    s=sidstr(ace['Sid'].getData()); acc=ace['Mask']['Mask']
                    desc=[k for k,m in ACCESS.items() if acc&m]
                    if desc:
                        mark=' <<< OURS' if s in OUR else ''
                        print(f"  {ace['TypeName']} sid={s} -> {'|'.join(desc)}{mark}")
                except Exception: pass
    except Exception as e:
        print(f"{dn}: sd parse err {e}")
conn.unbind()