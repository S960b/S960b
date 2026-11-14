# Old Trusty (Active Directory, two flags)

[По-русски](README.ru.md) | [Full solving path](solve.md)

**Task:** obtain one Administrator desktop flag on LEGACY DC and another on CORE DC, two domain controllers in trusted forests.

**Result:** **both flags recovered**, as documented in the original evidence. The saved platform API snapshot does not list Old Trusty as completed; platform submission of both flags is not independently established (public flags masked as `PP{...}`).

LEGACY path: `jdoe` AS-REP roast → BloodHound ACL chain (`WriteDacl`, `AddMember`, `AddKeyCredentialLink`) → `svc_ops` Shadow Credentials → Server Operators service-control rights → NTDS → Administrator → first flag.

CORE path: DRSR TDO trust material → `LEGACY$` TGT → PKI deployment XML on `Software$` → `svc_enrollment` → AD CS ESC3 with Enrollment Agent followed by a `User`-template Administrator certificate → PKINIT → second flag.

Most important failed attempt: `KDC_ERR_PADATA_TYPE_NOSUPP` because the final certificate lacked the EKU required for PKINIT. Switching the final certificate template to `User` made the route succeed.

**Evidence:** the supplied `OldTrusty/README.md` is a full 13 KB writeup, with `FLAG1.txt`, `FLAG2.txt`, Certipy output, XML configuration and Python scripts.

[Read the full technical walkthrough](solve.md).