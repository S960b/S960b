# Carbon

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Category:** fullpwn
- **Difficulty:** 1
- **Materials:** Two recovered flags recorded in the solved-task summary; no static challenge distribution, final exploit or host transcript preserved
- **Flags:** 2 accepted (`PP{...}`)

A two-flag machine challenge. The accepted outcomes are a user-level flag associated with cached-session replay and a root-level flag associated with the operator's environment.

**Root cause:** The task summary identifies two distinct trust boundaries: an application/user session that could be reused from cache, and privileged operator context whose environment contained a secret. Prior notes also mention investigation of a Next.js/React Flight attack surface. Because no final exploit log or deployed source was attached, the exact order and whether that independent RCE lead was part of the successful route cannot be determined.

See the [solving path](solve.md).
