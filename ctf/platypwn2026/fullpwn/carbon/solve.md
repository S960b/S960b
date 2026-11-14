# Carbon: solving notes

[По-русски](solve.ru.md)

## 1. Task

A two-flag machine challenge. The accepted outcomes are a user-level flag associated with cached-session replay and a root-level flag associated with the operator's environment.

Materials: Accepted user/root flags in agent report; no challenge image, exploit or host log included.

## 2. Analysis

The task summary identifies two distinct trust boundaries: an application/user session that could be reused from cache, and privileged operator context whose environment contained a secret. Prior notes also mention investigation of a Next.js/React Flight attack surface. Because no final exploit log or deployed source was attached, the exact order and whether that independent RCE lead was part of the successful route cannot be determined.

## 3. Solving procedure

1. Inspect the application endpoints, session handling and cache artifacts, then establish which cached session was reaccepted. The actual replay token, location and request bytes have been intentionally omitted because none were recorded in the supplied archive.
2. Use the recorded working session to obtain the user-level evidence. The agent confirmed the user flag.
3. Inspect the operator boundary for an environment-derived secret and establish a path to privileged read. The agent confirmed the root flag as well, but the specific escalation commands were not preserved.
4. Do not conflate the separately explored Next.js/React Flight path with proven execution without a saved successful trace.

## 4. Evidence

The final Git-bridge task lists both accepted flags, distinguished as user and root. A previous discussion mentions the cache and operator environment, but no raw replay or privilege-escalation terminal log is attached.

**Result:** 2 accepted flag(s), masked in the public repository (`PP{...}`).

## 5. Limits

This is an outcome-focused writeup, not a reproducible full exploit. The missing payload and post-exploitation commands are explicitly unidentified instead of invented.

## 6. Mitigation

Bind sessions to the intended principal and lifetime, invalidate cached credentials safely, prohibit reuse across security contexts and protect secrets stored in privileged process environments.
