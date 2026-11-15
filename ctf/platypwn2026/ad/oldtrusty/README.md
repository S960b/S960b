# Old Trusty (Active Directory, two flags)

[По-русски](README.ru.md) | [Full solving path](solve.md)

**Task:** obtain one Administrator desktop flag on LEGACY DC and another on CORE DC, two domain controllers in trusted forests.

**Result:** **both flags obtained and accepted by the competition platform; Old Trusty was credited as solved.** The two original flag files are included below as direct evidence.

LEGACY path: `jdoe` AS-REP roast → BloodHound ACL chain (`WriteDacl`, `AddMember`, `AddKeyCredentialLink`) → `svc_ops` Shadow Credentials → Server Operators service-control rights → NTDS → Administrator → first flag.

CORE path: DRSR TDO trust material → `LEGACY$` TGT → PKI deployment XML on `Software$` → `svc_enrollment` → AD CS ESC3 with Enrollment Agent followed by a `User`-template Administrator certificate → PKINIT → second flag.

**Accepted flags:**

- **LEGACY / FLAG1:** `PP{d4cl-4cls-sh4d0ws-4nd-s3rv1c3s}` — [original `FLAG1.txt`](evidence/FLAG1.txt)
- **CORE / FLAG2:** `PP{trvst-th3-c3rt1f1c4t3-ch41n}` — [original `FLAG2.txt`](evidence/FLAG2.txt)

Most important failed attempt: `KDC_ERR_PADATA_TYPE_NOSUPP` because the final certificate lacked the EKU required for PKINIT. Switching the final certificate template to `User` made the route succeed.

**Evidence:** the original technical report, Certipy output, PKI XML and 13 Python helpers, plus the complete recovered [FLAG1](evidence/FLAG1.txt) and [FLAG2](evidence/FLAG2.txt).

[Read the full technical walkthrough](solve.md).