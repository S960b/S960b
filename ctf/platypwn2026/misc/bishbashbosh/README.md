# Bish Bash Bosh

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Category:** misc
- **Difficulty:** 1
- **Materials:** `server.sh`, `Dockerfile`, `entrypoint.sh`, compose; exploit command in evidence
- **Flags:** 1 accepted (`PP{...}`)

A small Bash menu asks for a number from 0 through 2 and prints one of three messages, but arithmetic expansion is performed on attacker-controlled input.

**Root cause:** The script runs `read -r i` and then `let i=$i-1`, followed by a bounds check before `cat -- "${files[i]}"`. Bash's arithmetic evaluator interprets input containing array syntax and command substitution; the shell can execute the substitution while evaluating `let`, before the bounds check ever rejects the value. The `files` array even includes `/flag/flag.txt` in its unused first slot, but directly selecting an out-of-range menu entry is not the working method.

[Detailed walkthrough](solve.md).
