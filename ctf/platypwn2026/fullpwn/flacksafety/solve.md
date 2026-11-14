# Flacksafety: solving notes

[По-русски](solve.ru.md)

## 1. Task

A multi-stage backup and governance challenge. Only the confirmed governance flag is presented here; the older backup repository stage remained incomplete.

Materials: `shadow.txt`, agent bridge governance investigation and later incomplete stage-six audit.

## 2. Analysis

The distributed attachment is only `shadow.txt` with an Argon2id-derived login, whose password was reported recovered as `iloveme`. After foothold, the identified governance weakness was a profile configuration with mode `rw-rw-rw-` and a `run-before` hook invoked through supercronic. A writable configuration can select a command executed by a more privileged scheduled process. The agent's later report says 5/6 destruction actions were completed and the governance flag was accepted; stage 6 required `LEGACY_REST_PASSWORD` for an old rclone/restic repository and was not solved.

## 3. Solving procedure

1. Inspect the supplied hash and investigate the permitted employee login. The hash-to-password recovery was confirmed in the agent's report, but a raw cracking transcript is absent.
2. Trace supercronic jobs and their profile config. A world-writable profile (`rw-rw-rw-`) provides a route to control `run-before`.
3. The governance stage was completed by exercising this scheduler/configuration trust boundary and yielded one accepted flag.
4. An additional legacy backup stage was investigated with file operations and Kubernetes-related checks. Attempts to obtain `LEGACY_REST_PASSWORD` did not succeed before the CTF instance became unreachable.

## 4. Evidence

The archive contains `shadow.txt`. The agent's detailed message identifies the writable profile, hook, completed governance stage, unsuccessful legacy backup stage and subsequent network timeout. The final task list confirms only the governance flag.

**Result:** 1 accepted flag(s), masked in the public repository (`PP{...}`).

## 5. Limits

Exact hook payload, target profile path and full terminal record are not preserved. This writeup deliberately does not label the unsolved backup stage as completed.

## 6. Mitigation

Protect scheduled-task configuration from modification by less privileged users; avoid privileged hooks in world-writable files. Separate backup credentials and minimize Kubernetes/service-account access.
