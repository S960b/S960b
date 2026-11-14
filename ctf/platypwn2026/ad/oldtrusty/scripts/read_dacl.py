#!/usr/bin/env python3
# Read nTSecurityDescriptor (owner+group+DACL) for key objects using the
# ldap3 SD flags control (0x04=DACL included, plus 0x01/0x02 owner+group).
# Parse with impacket SR_SECURITY_DESCRIPTOR -> list effective ACEs.
from ldap3 import Server, Connection, NTLM, ENCRYPT
try:
    from ldap3.protocol.microsoft import security_descriptor_control
    HAVE_SDCTRL = True
except Exception as ex:
    HAVE_SDCTRL = False
    print("WARN no security_descriptor_control:", ex)
from impacket.ldap.ldaptypes import SR_SECURITY_DESCRIPTOR

PW="<PW>"
c=Connection(Server("<CORE_DC_IP>",port=389,use_ssl=False),user=r"CORE\svc_enrollment",password=PW,
             authentication=NTLM,session_security=ENCRYPT,auto_bind=True,auto_referrals=False,receive_timeout=15)
print("bound:", c.bound)

def sid_str(sd, sid):
    try:
        return sid.formatCanonical()
    except Exception:
        return repr(sid)[:40]

def show(label, dn):
    controls = None
    if HAVE_SDCTRL:
        # 0x02 GROUP,0x01 OWNER,0x04 DACL,0x08 SACL  -> owner+group+DACL = 0x07;
        # security_descriptor_control() already returns a flat list.
        controls = security_descriptor_control(sdflags=0x07)
    try:
        ok = c.search(dn, "(objectClass=*)", attributes=["nTSecurityDescriptor"], controls=controls)
        if not ok:
            print(f"[{label}] search failed: result={c.result['result']} {c.result['description']} {c.result.get('message','')}")
            return
        e = c.entries[0]
        sd_attr = getattr(e, "nTSecurityDescriptor", None)
        if sd_attr is None or not sd_attr.value:
            print(f"[{label}] nTSecurityDescriptor empty/absent (server denied or attr stripped)")
            return
        raw = sd_attr.raw_values[0] if hasattr(sd_attr,'raw_values') else bytes(sd_attr.value)
        sd = SR_SECURITY_DESCRIPTOR()
        sd.fromString(raw)
        print(f"\n===== {label} :: {dn} =====")
        try:
            print("  Owner :", sd['OwnerSid'].formatCanonical())
        except Exception: pass
        try:
            print("  Group :", sd['GroupSid'].formatCanonical())
        except Exception: pass
        dacl = sd['Dacl']
        if dacl is None:
            print("  DACL : (null)")
            return
        print("  DACL aces:", dacl['AclSize'] if 'AclSize' in dacl else '?')
        for ace in dacl['Aces']:
            typ = ace['Header']['AceType']
            rights = hex(ace['Mask']['Mask']) if 'Mask' in ace and ace['Mask'] else '?'
            try:
                sid = ace['Sid'].formatCanonical()
            except Exception:
                sid = '?'
            flags = ace['Header']['AceFlags']
            obj = ''
            if 'ObjectType' in ace and ace['ObjectType']:
                try:
                    obj = '/guid='+ace['ObjectType'].__str__()
                except Exception:
                    obj='/guid?'
            tn = {0x00:'ALLOW',0x01:'DENY',0x05:'ALLOW-obj',0x06:'DENY-obj',0x02:'AUDIT'}.get(typ, hex(typ))
            print(f"    ACE {tn} rights={rights} flags={hex(flags)} sid={sid}{obj}")
        # print DACL blob length diagnostic (did server return binary descriptor, not error)
        print(f"  [diag] DACL raw len={len(raw)} result={c.result['result']} ({c.result['description']})")
    except Exception as ex:
        print(f"[{label}] EXC: {repr(ex)[:160]}")

for lbl,dn in [
    ("svc_enrollment","CN=svc_enrollment,CN=Users,DC=core,DC=corp"),
    ("Administrator","CN=Administrator,CN=Users,DC=core,DC=corp"),
    ("LEGACY-trust","CN=LEGACY$,CN=ForeignSecurityPrincipals,DC=core,DC=corp"),
]:
    show(lbl, dn)
c.unbind()