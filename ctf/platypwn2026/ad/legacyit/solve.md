# Legacy IT: solving notes

[По-русски](solve.ru.md)

## 1. Task

An Active Directory dynamic-instance challenge (no static attachment was supplied) solved through a legacy authentication path, ending with administrator access to a file on the domain controller.

Materials: Two saved Impacket SMB commands; no server source or credential-origin transcript.

## 2. Vulnerability

The preserved command uses `impacket-smbclient` with NT-hash authentication (`-hashes :<masked NT hash>`), the domain `easyad.local`, and account `Administrator`. It then selects the administrative `C$` share, lists `Users\Administrator\Desktop`, and reads `flag.txt`. This proves the final access method. The task summary calls the weakness legacy-authentication/backward compatibility, but there is no trace showing where the administrator NT hash came from or which legacy setting exposed it.

## 3. Procedure

1. The earlier reconnaissance and credential acquisition stage is not recorded. The challenge summary associates it with legacy authentication, but a specific protocol mechanism cannot be verified.
2. With the recovered NT hash, authenticate to the lab domain controller over SMB using Impacket's pass-the-hash support: `impacket-smbclient -hashes :<NT_HASH> 'easyad.local/Administrator@<host>'`.
3. In the SMB client, run `use C$`, then `ls Users\\Administrator\\Desktop`, and `cat Users\\Administrator\\Desktop\\flag.txt`.
4. The resulting flag was accepted on the platform.

## 4. Evidence and result

The ZIP contains two duplicate `evidence/solve_*.txt` records with the complete Impacket command and file path. Both contain a credential and an internal host address, which are masked here. The final agent report confirms the accepted flag.

Result: 1 accepted flag, full string masked (`PP{...}`).

## 5. Reproduction limits

The source of the NT hash and the exact backward-compatibility weakness are not documented. The proof supports only the final authenticated SMB read, not a detailed AS-REP/NTLM/LDAP exploitation narrative.

## 6. Mitigation

Disable obsolete authentication protocols where possible, restrict privileged NT-hash exposure and lateral movement, monitor pass-the-hash access, and limit administrative share access.
