# Flacksafety (fullpwn, partial solve)

[По-русски](README.ru.md) | [Full technical investigation](solve.md)

**Result:** governance flag accepted; **five of six** backup tiers checked as Destroyed. `Old Rclone Remote` remained Intact and the second flag was **not** obtained.

**Initial foothold:** Argon2id hash in `shadow.txt` → employee SSH. A world-writable `/etc/resticprofile/profiles.yaml` consumed by root's supercronic job enabled a privileged `run-before` hook and discovery of backup service credentials.

**Main investigation:** rclone 1.74.3 `serve restic --private-repos` over an SFTP backend. Path traversal was confirmed for GET/POST/DELETE of known files, including a read of the server entrypoint. Directory listing across the private-account boundary was filtered out because canonical and raw paths differed. The sixth checker test required an **entirely empty directory**; the team lacked the object names and separate legacy account password.

**Evidence:** shipped `shadow.txt` and detailed agent session reports containing controlled HTTP probes, checker source, server configuration and failed hypotheses. Public flags, live IPs and credentials are masked.

[Read the detailed technical walkthrough](solve.md).
