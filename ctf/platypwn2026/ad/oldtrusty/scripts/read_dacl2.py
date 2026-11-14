#!/usr/bin/env python3
from ldap3 import Server, Connection, NTLM, ENCRYPT
from ldap3.protocol.microsoft import security_descriptor_control
from impacket.ldap.ldaptypes import SR_SECURITY_DESCRIPTOR

PW="<PW>"
c=Connection(Server("<CORE_DC_IP>",port=389,use_ssl=False),user=r"CORE\svc_enrollment",password=PW,
             authentication=NTLM,session_security=ENCRYPT,auto_bind=True,auto_referrals=False,receive_timeout=15)

def get_dn(sam):
    c.search("DC=core,DC=corp", f"(sAMAccountName={sam})", attributes=["distinguishedName","objectSid"])
    if c.entries:
        return c.entries[0].entry_dn, None
    return None, ("result=%s %s %s"%(c.result['result'],c.result['description'],c.result.get('message','')))

def rights_names(mask):
    m=int(mask)
    G={0x1:"GENERIC_READ",0x2:"GENERIC_WRITE",0x4:"GENERIC_EXECUTE",0x8:"GENERIC_ALL",
       0x10000:"DELETE",0x20000:"READ_CONTROL",0x40000:"WRITE_DAC",0x80000:"WRITE_OWNER",
       0x20000|0x10000:"SYNCHRONIZE"}
    # standard + specific; print key bits
    names=[]
    bits={0x1:"GenericRead",0x2:"GenericWrite",0x4:"GenericExec",0x8:"GenericAll",
          0x100:"List/ReadChild",0x200:"WriteSelf",0x400:"WriteProp",0x800:"ExtendedRight",
          0x10000:"Delete",0x20000:"ReadControl",0x40000:"WriteDacl",0x80000:"WriteOwner",
          0x200000:"WriteExtendedRight",0x400000:"AddChild",0x800000:"DeleteChild"}
    for b,n in sorted(bits.items(), reverse=True):
        if m & b:
            names.append(n)
    return "+".join(names) if names else hex(m)

def show(sam, label=None):
    label = label or sam
    dn,err = get_dn(sam)
    if dn is None:
        print(f"[{label}] DN not found ({err})")
        return
    try:
        controls = security_descriptor_control(sdflags=0x07)
        c.search(dn, "(objectClass=*)", attributes=["nTSecurityDescriptor"], controls=controls)
    except Exception as e:
        print(f"[{label}] search exc {repr(e)[:120]}")
        return
    if not c.entries:
        print(f"[{label}] r={c.result['result']} {c.result['description']} (server denied SD read)")
        return
    sd_attr = None
    try:
        sd_attr = c.entries[0].nTSecurityDescriptor
    except Exception:
        pass
    if sd_attr is None:
        print(f"[{label}] no nTSecurityDescriptor attribute returned")
        return
    raw = sd_attr.raw_values[0]
    sd = SR_SECURITY_DESCRIPTOR(); sd.fromString(raw)
    print(f"\n===== {label} ({dn}) =====")
    try: print("  Owner:", sd['OwnerSid'].formatCanonical())
    except Exception as e: print("  Owner: ?", e)
    try: print("  Group:", sd['GroupSid'].formatCanonical())
    except Exception as e: print("  Group: ?", e)
    try:
        dacl = sd['Dacl']
        if dacl is None:
            print("  DACL: (null)"); return
        print("  DACL aces=%d ->" % len(dacl['Aces']))
        for ace in dacl['Aces']:
            try:
                h=ace['Header']; t=h['AceType']; sid=ace['Sid'].formatCanonical(); m=ace['Mask']['Mask']
                tn={0:'ALLOW',1:'DENY',2:'AUDIT',5:'ALLOW-obj',6:'DENY-obj'}.get(t,hex(t))
                print(f"    {tn:9s} {rights_names(m):34s} -> {sid}")
            except Exception as e:
                print("    (parse-ace err:", repr(e)[:80], ")")
    except Exception as e:
        print("  DACL parse err:", repr(e)[:120], "(Dacl key in sd:", 'Dacl' in sd, ")")
    print(f"  [diag] raw SD len={len(raw)} result={c.result['result']} {c.result['description']}")

for sam in ("Administrator","svc_enrollment","DC02$","LEGACY$"):
    show(sam)
c.unbind()