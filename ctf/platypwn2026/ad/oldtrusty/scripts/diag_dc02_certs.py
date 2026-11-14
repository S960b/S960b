#!/usr/bin/env python3
from ldap3 import Server, Connection, NTLM, ENCRYPT, SUBTREE, BASE
import base64, hashlib, datetime, sys

PW="<PW>"
HOST="<DC02_IP>"
server = Server(HOST, port=389, use_ssl=False, connect_timeout=12)
conn = Connection(server, user=r"CORE\svc_enrollment", password=PW,
                  authentication=NTLM, session_security=ENCRYPT,
                  auto_bind=True, auto_referrals=False, receive_timeout=20)
print("LDAP bind:", conn.bound)
rb = "DC=core,DC=corp"
cfg = "CN=Configuration,DC=core,DC=corp"

def sec_filter(s):
    return ''.join(c for c in str(s) if c in '0123456789.-+/')

def dumponly(dn, f, attrs, label, scope=SUBTREE):
    try:
        ok = conn.search(dn, f, search_scope=scope, attributes=attrs)
        if not ok:
            print(f"\n[{label}] search returned nothing (rc={conn.result['result']} {conn.result['description']})")
            return
        for e in conn.entries:
            print(f"\n===== {label} :: {e.entry_dn} =====")
            for a in attrs:
                if not hasattr(e, a):
                    continue
                attr = getattr(e, a)
                v = attr.value
                try:
                    raw = attr.raw_values if hasattr(attr,'raw_values') else None
                except Exception:
                    raw = None
                if isinstance(v,(list,tuple)) and len(v)>1:
                    print(f"  {a}: {len(v)} value(s)")
                    for x in (raw or v)[:6]:
                        if isinstance(x,(bytes,bytearray)):
                            print("     bin len", len(x), "sha1", hashlib.sha1(bytes(x)).hexdigest()[:16])
                        else:
                            print("    ", sec_filter(x)[:120])
                elif isinstance(v,(bytes,bytearray)):
                    print(f"  {a}: bin len {len(v)} sha1 {hashlib.sha1(bytes(v)).hexdigest()[:16]}")
                else:
                    print(f"  {a}: {sec_filter(v)[:160]}")
    except Exception as ex:
        print(f"\n[{label}] ERROR: {repr(ex)[:140]}")

# 1) DC02 computer object
dumponly("CN=Computers,"+rb, "(sAMAccountName=DC02$)",
     ["dNSHostName","userCertificate","msDS-KeyCredentialLink","description","objectSid"], "DC02 COMPUTER")

# 2) CORE-CA
dumponly(cfg, "(cn=CORE-CA)",
     ["cn","cACertificate","certificateTemplates","dNSHostName","name"], "CA CORE-CA")

# 3) All enrollment services / CAs
dumponly("CN=Enrollment Services,CN=Public Key Services,CN=Services,"+cfg, "(objectClass=pKIEnrollmentService)",
     ["cn","dNSHostName","cACertificate"], "ENROLLMENT SERVICES")

# 4) KDS root keys
dumponly(cfg, "(objectClass=msKds-RootKey)", ["cn","msKds-RootKeyData"], "KDS ROOT KEY")

# 5) Servers (DC objects) machines
dumponly("CN=Servers,CN=Default-First-Site-Name,CN=Sites,"+cfg, "(objectClass=server)",
     ["cn","dnsHostName","userCertificate"], "SERVERS")

# 6) DC02 in Domain Controllers OU
dumponly("OU=Domain Controllers,"+rb, "(sAMAccountName=DC02$)",
     ["sAMAccountName","userCertificate","msDS-KeyCredentialLink"], "DC02 DC OBJECT")

# 7) All users -- search for userCertificate presence (count)
try:
    ok = conn.search(rb, "(&(objectClass=user)(userCertificate=*))", search_scope=SUBTREE,
                     attributes=["sAMAccountName","objectSid"])
    print("\nUSERS WITH userCertificate:", len(conn.entries))
    for e in conn.entries:
        print("   user:", e.sAMAccountName)
except Exception as ex:
    print("userCert scan err", repr(ex)[:120])

# 8) KDC cert usage config: list of CA using 'KDC Authentication' template / msPKI
dumponly("CN=Certificate Templates,CN=Public Key Services,CN=Services,"+cfg, "(mspki-certificate-name-flag=*)",
     ["cn","msPKI-Certificate-Name-Flag","msPKI-Enrollment-Flag","msPKI-Template-Schema-Version","msPKI-Cert-Template-OID","msPKI-Template-Minor-Revision"], "CERT TEMPLATES (all)")

conn.unbind()
print("\nDONE")