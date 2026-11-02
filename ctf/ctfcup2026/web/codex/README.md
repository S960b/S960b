# Codex (Святой Кодекс)

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 115
- **Category:** web
- **Task:** https://game.ctfcup.online/tasks/codex
- **Attachment:** `codex.zip`, with the server, bot and frontend sources
- **Flag:** `ctfcup{...}` (masked)

A Bible-reader app with comments and an administrator bot. The bot stores the flag as a private note on page `matthew.7`, with `scope` equal to its own `BOT_UUID`. In this version that UUID is `process.env.BOT_UUID || 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'`, and the deployment overrides only the flag, so the default is what runs.

The solve is three requests: read the scope (empty), trigger `/api/visit` so the bot plants the note, read the scope again. The suffix of the flag is issued per deployment, so it has to be read from the live instance.

Automation: `scripts/solve_codex.py <instance-url>`. The Revenge variant randomizes the UUID per process and protects admin notes with an extra cookie, which is why the same script returns nothing there.
