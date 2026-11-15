# Legacy IT

[Русская версия](README.ru.md) | [Detailed walkthrough](solve.md)

**Category:** Active Directory · **Difficulty:** 1 · **Status:** accepted by the platform

Anonymous SMB `Public/onboarding.txt` exposed the `trainee` credentials. Excessive `FullControl` on the `Legacy IT Admins` group enabled self-membership, DCSync of the domain Administrator hash and SMB pass-the-hash access to the desktop flag.

Flag: `PP{l3g4cy-1t-4dm1ns-4k4-w1nd0ws-4dm1ns::FqCiLH_9Sp7u}`.

[Full technical solution](solve.md) · [Evidence checkpoints](evidence/session-checkpoints.md)
