#!/usr/bin/env python3
from ldap3 import Server, Connection, NTLM, ENCRYPT, SUBTREE
PW="<PW>"
server = Server("<DC02_IP>", port=389, use_ssl=False, connect_timeout=12)
conn = Connection(server, user=r"CORE\svc_enrollment", password=PW,
                  authentication=NTLM, session_security=ENCRYPT,
                  auto_bind=True, auto_referrals=False, receive_timeout=20)
print("LDAP bind:", conn.bound)
try:
    print("whoami:", conn.extend.standard.who_am_i())
except Exception as e:
    print("whoami err:", repr(e))
try:
    conn.search("DC=core,DC=corp", "(objectClass=user)", search_scope=SUBTREE,
                attributes=["sAMAccountName","mail","userPrincipalName","objectSid","memberOf"])
    for e in conn.entries:
        print("USER:", e.sAMAccountName, "| mail=", (e.mail.value if hasattr(e,'mail') and e.mail else ''), "| upn=", (e.userPrincipalName.value if e.userPrincipalName else ''))
except Exception as ex:
    print("search err:", repr(ex))
conn.unbind()