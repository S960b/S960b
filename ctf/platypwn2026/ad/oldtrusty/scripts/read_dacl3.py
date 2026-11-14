#!/usr/bin/env python3
from ldap3 import Server, Connection, NTLM, ENCRYPT
from ldap3.protocol.microsoft import security_descriptor_control
from impacket.ldap.ldaptypes import SR_SECURITY_DESCRIPTOR, ACCESS_ALLOWED_ACE, ACCESS_DENIED_ACE, ACCESS_ALLOWED_OBJECT_ACE, ACCESS_DENIED_OBJECT_ACE, SYSTEM_AUDIT_ACE, SYSTEM_AUDIT_OBJECT_ACE
import struct

ACE_TYPES = {0x00:ACCESS_ALLOWED_ACE,0x01:ACCESS_DENIED_ACE,0x02:SYSTEM_AUDIT_ACE,
             0x05:ACCESS_ALLOWED_OBJECT_ACE,0x06:ACCESS_DENIED_OBJECT_ACE,0x07:SYSTEM_AUDIT_OBJECT_ACE}

def rights_names(m):
    m=int(m); names=[]
    bits=[(0x1,"GenericRead"),(0x2,"GenericWrite"),(0x4,"GenericExec"),(0x8,"GenericAll"),
          (0x100,"ReadProp"),(0x200,"WriteSelf"),(0x400,"WriteProp"),(0x800,"ExtRight"),
          (0x10000,"Delete"),(0x20000,"ReadCtrl"),(0x40000,"WriteDacl"),(0x80000,"WriteOwner"),
          (0x200000,"AddMember?/WriteExt"),(0x400000,"AddChild"),(0x800000,"DeleteChild")]
    for b,n in sorted(bits,reverse=True):
        if m & b: names.append(n)
    return "+".join(names) if names else hex(m)

provider_map=None
def parse_aces(dacl, indent="    "):
    out=[]
    try:
        aces = list(dacl['Data'])  # impacket ACL lazily parses Data into ACE objects
    except Exception as e:
        # fallback: raw bytes
        return [("?",0,'?',0,'raw-parse-err:%r'%e)]
    for ace in aces:
        try:
            typ = ace['Header']['AceType']
        except Exception:
            typ = 0
        try:
            sid = ace['Sid'].formatCanonical()
        except Exception:
            sid = '?'
        try:
            mask = ace['Mask']['Mask']
        except Exception:
            mask = 0
        try:
            size = len(ace.getData()) if hasattr(ace,'getData') else 0
        except Exception:
            size = 0
        tn={0:'ALLOW',1:'DENY',2:'AUDIT',5:'ALLOW-obj',6:'DENY-obj'}.get(typ,hex(typ))
        out.append((tn, mask, sid, size))
    return out

PW="<PW>"
c=Connection(Server("<CORE_DC_IP>",port=389,use_ssl=False),user=r"CORE\svc_enrollment",password=PW,
             authentication=NTLM,session_security=ENCRYPT,auto_bind=True,auto_referrals=False,receive_timeout=15)

def show(sam):
    c.search("DC=core,DC=corp", f"(sAMAccountName={sam})", attributes=["distinguishedName"])
    if not c.entries:
        print(f"[{sam}] unknown"); return
    dn=c.entries[0].entry_dn
    c.search(dn,"(objectClass=*)", attributes=["nTSecurityDescriptor"],
             controls=security_descriptor_control(sdflags=0x07))
    if not c.entries:
        print(f"[{sam}] SD denied rc={c.result['result']} {c.result['description']} {c.result.get('message','')}")
        return
    raw=c.entries[0].nTSecurityDescriptor.raw_values[0]
    sd=SR_SECURITY_DESCRIPTOR(); sd.fromString(raw)
    print(f"\n===== {sam} :: {dn} =====")
    print("  Owner:", sd['OwnerSid'].formatCanonical(), " Group:", sd['GroupSid'].formatCanonical())
    dacl=sd['Dacl']
    if dacl is None:
        print("  DACL: NULL"); return
    for tn,mask,sid,size in parse_aces(dacl):
        print(f"    {tn:9s} {rights_names(mask):32s} {size:4d}B -> {sid}")

for sam in ("Administrator","svc_enrollment","DC02$","LEGACY$"):
    show(sam)
c.unbind()
print("\nDONE")