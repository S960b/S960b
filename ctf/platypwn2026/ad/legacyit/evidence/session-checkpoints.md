# Legacy IT: session checkpoints

Chronology from the November 13 competition log, with account password, hash and instance address redacted.

- 23:18 — anonymous `Public` share read of `onboarding.txt`: **Anonymous login successful**, `easyad.local/trainee` credentials found.
- 23:25 — group ACL investigation identified `FullControl` over `Legacy IT Admins` for `trainee`.
- 23:26 — `ldap3` modification of `member`: **mod: True success**. Group now contained `CN=Jamie Smith,CN=Users,DC=easyad,DC=local`.
- 23:27 — `impacket-secretsdump -just-dc-user Administrator`: **Using the DRSUAPI method to get NTDS.DIT secrets**, Administrator hash returned.
- 23:28 — `impacket-smbclient` after `use C$`: desktop flag printed.

```text
PP{l3g4cy-1t-4dm1ns…9Sp7u}
```

The platform accepted the challenge. These are contemporaneous checks, not a hypothetical reconstruction.
