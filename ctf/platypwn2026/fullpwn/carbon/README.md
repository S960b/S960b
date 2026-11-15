# Carbon

[Русская версия](README.ru.md) | [Detailed solution](solve.md)

**Category:** Fullpwn · **Difficulty:** 1 · **Result:** both flags accepted

The chain started with React Server Components deserialization on Next.js 15.5.6, leading to execution as `nextjs`. Internal ops notes led to a Redis-backed `session:operator` token; replaying it against the admin API revealed the operator password. The operator account then had NOPASSWD sudo rights for `/usr/bin/env`, which gave root.

**User:** `PP{c4rb0n_c4ch3d_s3…Kt36U}`  
**Root:** `PP{0p3r4t0r_3nv_1s_…_dtEE}`

[Technical walkthrough](solve.md) · [Execution checkpoints](evidence/session-checkpoints.md)
