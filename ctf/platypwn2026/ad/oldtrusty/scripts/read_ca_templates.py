#!/usr/bin/env python3
# Decode the certificateTemplates OIDs issued by CORE-CA to know exactly
# which templates the CA supports (and their EKU profile).
from ldap3 import Server, Connection, NTLM, ENCRYPT
import struct

# template OID -> name map (from earlier certipy find on CORE-CA)
TM = {
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.1":"User",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.2":"UserSignature",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.3":"SmartcardUser",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.4":"ClientAuth",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.5":"SmartcardLogon",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.6":"EFS",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.7":"Administrator",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.8":"EFSRecovery",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.9":"CodeSigning",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.10":"CTLSigning",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.11":"EnrollmentAgent",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.12":"EnrollmentAgentOffline",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.13":"MachineEnrollmentAgent",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.14":"Machine",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.15":"DomainController",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.16":"WebServer",
 "1.3.6.1.4.1.311.21.8.4622294.4159505.10702747.7672845.7907897.236.1.17":"CA",
}
def der_oid_bytes_header(b):
    return b

def oid_from_der(der):
    # simple OID parser over the encoded value (each value is raw OID bytes)
    b = der
    out=[]
    # first byte: first two arcs
    f = b[0]; out.append(str(f//40)); out.append(str(f%40)); i=1
    while i < len(b):
        val=0
        while True:
            x=b[i]; i+=1; val=(val<<7)|(x&0x7f)
            if not (x&0x80): break
        out.append(str(val))
    return ".".join(out)

PW="<PW>"
c=Connection(Server("<CORE_DC_IP>",port=389,use_ssl=False),user=r"CORE\svc_enrollment",password=PW,
             authentication=NTLM,session_security=ENCRYPT,auto_bind=True,auto_referrals=False,receive_timeout=15)
base="CN=Enrollment Services,CN=Public Key Services,CN=Services,CN=Configuration,DC=core,DC=corp"
c.search(base,"(cn=CORE-CA)",attributes=["cn","certificateTemplates","dNSHostName"])
for e in c.entries:
    print("CA:", e.cn.value, "host:", e.dNSHostName.value if hasattr(e,'dNSHostName') and e.dNSHostName else '')
    vals = e.certificateTemplates.value if hasattr(e,'certificateTemplates') and e.certificateTemplates else None
    if vals is None:
        print("  certificateTemplates: (none returned)")
    else:
        if not isinstance(vals,list): vals=[vals]
        print("  supported templates (%d):"%len(vals))
        for v in vals:
            raw = v.raw if hasattr(v,'raw') else v
            if isinstance(raw, str):
                raw = raw.encode('latin1')  # ldap3 sometimes surfaces binary as latin-1 str
            raw = bytes(raw)
            try:
                oid=oid_from_der(raw)
            except Exception as ex:
                oid="UNPARSE(%s, %s)"%(raw.hex(), ex)
            print("    ", TM.get(oid, "<?>"), "=", oid)
c.unbind()