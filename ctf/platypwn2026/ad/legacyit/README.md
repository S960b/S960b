# Legacy IT

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Category:** ad
- **Difficulty:** 1
- **Materials:** Two saved Impacket SMB commands; no server source or credential-origin transcript
- **Flags:** 1 accepted (`PP{...}`)

An Active Directory challenge solved through a legacy authentication path, ending with administrator access to a file on the domain controller.

**Root cause:** The preserved command uses `impacket-smbclient` with NT-hash authentication (`-hashes :<masked NT hash>`), the domain `easyad.local`, and account `Administrator`. It then selects the administrative `C$` share, lists `Users\Administrator\Desktop`, and reads `flag.txt`. This proves the final access method. The task summary calls the weakness legacy-authentication/backward compatibility, but there is no trace showing where the administrator NT hash came from or which legacy setting exposed it.

[Detailed walkthrough](solve.md).
