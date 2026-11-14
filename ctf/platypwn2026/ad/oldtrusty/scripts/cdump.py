#!/usr/bin/env python3
import os
from impacket.ldap import ldap as l_ldap
from impacket.ldap import ldapasn1
os.environ['KRB5CCNAME']='/home/kali/LEGACY$.ccache'
conn=l_ldap.LDAPConnection('ldaps://<DC02_IP>','DC=core,DC=corp','<DC02_IP>')
conn.kerberosLogin('LEGACY$','','core.corp','','',None,kdcHost='<DC02_IP>')

def attrs(item):
    d={}
    for attribute in item['attributes']:
        t=str(attribute['type']); vals=[]
        for v in attribute['vals']:
            try: vals.append(v.asOctets().decode('utf-8','replace'))
            except Exception: vals.append(str(v))
        d[t]=vals
    return d

print("==== ALL USERS ====")
for item in conn.search(searchFilter="(objectClass=user)", attributes=['sAMAccountName','memberOf','description','servicePrincipalName','userAccountControl']):
    if not isinstance(item,ldapasn1.SearchResultEntry): continue
    a=attrs(item)
    print("USER", a.get('sAMAccountName'), "| desc=", a.get('description'), "| SPN=", a.get('servicePrincipalName'))
    for m in a.get('memberOf',[]): print("     memberOf:", m)

print("==== GROUPS (privileged) ====")
for item in conn.search(searchFilter="(objectClass=group)", attributes=['sAMAccountName','member']):
    if not isinstance(item,ldapasn1.SearchResultEntry): continue
    a=attrs(item); cn=a.get('sAMAccountName',[''])[0]
    if any(k in cn.upper() for k in ['ADMIN','OPERATOR','CERT','ENROLL','SERVER','SUPPORT','BACKUP','ACCOUNT','PRINT','IPC','CRYPTO']):
        print("GROUP", cn, f"({len(a.get('member',[]))})")
        for m in a.get('member',[]): print("    -", m)
conn.close()