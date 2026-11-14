# Flacksafety

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Category:** fullpwn
- **Difficulty:** 3
- **Materials:** `shadow.txt`, agent bridge governance investigation and later incomplete stage-six audit
- **Flags:** 1 accepted (`PP{...}`)

A multi-stage backup and governance challenge. Only the confirmed governance flag is presented here; the older backup repository stage remained incomplete.

**Root cause:** The distributed attachment is only `shadow.txt` with an Argon2id-derived login, whose password was reported recovered as `iloveme`. After foothold, the identified governance weakness was a profile configuration with mode `rw-rw-rw-` and a `run-before` hook invoked through supercronic. A writable configuration can select a command executed by a more privileged scheduled process. The agent's later report says 5/6 destruction actions were completed and the governance flag was accepted; stage 6 required `LEGACY_REST_PASSWORD` for an old rclone/restic repository and was not solved.

See the [solving path](solve.md).
