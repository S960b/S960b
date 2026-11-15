# Carbon: React Server Components RCE, operator session replay and root

[По-русски](solve.ru.md)

## 1. Objective and reconnaissance

The Platypus Freight emissions portal was reachable at `/portal` on port 3000. Its footer identified **Next.js 15.5.6**. The two goals were `/home/operator/user.txt` and `/root/root.txt`.

The visible `queueEmissionsReport` server action accepted an account identifier, quarter and report kind. The application validated those fields strictly. Attempted command injection through the form and guessed `/admin` or `/api` routes were unproductive. The useful boundary was **React Flight / RSC deserialization before the application-specific form handler**.

## 2. From RSC to command execution as nextjs

The working test followed the React2Shell class of deserialization weaknesses (CVE-2025-55182) in the vulnerable Next.js/React combination. An initial proof of concept had failed; the successful request used a multipart body with fields `0` and `1` and an otherwise arbitrary `Next-Action: x` header. The second field contained the React Flight reference `"$@0"`. The first field supplied a crafted model that reached the Flight promise/prototype resolution logic.

The original probe redirected encoded command output through the framework's `NEXT_REDIRECT` mechanism, making the `x-action-redirect` response header a low-noise output channel. The checkpoint was:

```text
POST /portal
Next-Action: x
Content-Type: multipart/form-data; boundary=...

HTTP/1.1 303 See Other
x-action-redirect: http://x/<base64-command-output>;push

decoded output:
uid=1001(nextjs) gid=1001(nextjs) groups=1001(nextjs)
```

The verified output matters: a generic 500 digest from an earlier Server Action request was **not** proof of execution. The encoded `id` result was.

## 3. Local reconnaissance and the operator session

Execution began as the restricted `nextjs` user; directly reading the operator's file did not succeed. The working follow-up inspected environment and local configuration. The portal's `.env.local` contained an obsolete reporting database URL and pointed to `/opt/carbon/ops-notes.md`.

The ops notes described the intended privileged login flow:

1. The internal Redis service listens at `127.0.0.1:6379` and has no password on its loopback interface.
2. The short-lived operator token is stored under `session:operator`; the dashboard refreshes it.
3. The internal admin service at `127.0.0.1:9000` accepts this token as a Bearer credential at `/admin/operator-credential`, returning the operator's password.

The recorded command was equivalent to:

```bash
TOKEN=$(redis-cli --raw GET session:operator)
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9000/admin/operator-credential
```

The significant mistake was not a broken Redis cipher: the attacker already had a shell-like execution context in the same container/network namespace as an unauthenticated cache holding a reusable authentication token. The admin API treated possession of the cached token as sufficient authority to reveal another account's password.

## 4. Becoming operator and reading the first flag

Using the returned password, the successful run called `su operator` under a pseudo-terminal created with `script -qec`; a small delay ensured that the password reached the interactive prompt. The same session read the user flag and inspected sudo privileges:

```text
Password:
PP{c4rb0n_c4ch3d_s3ss10n_r3pl4y::aBfyfXvKt36U}

User operator may run the following commands:
    (root) NOPASSWD: /usr/bin/env
```

The first flag came from `/home/operator/user.txt`. It was accepted by the platform.

## 5. Root via an overly broad sudo rule

The `operator` account was allowed to execute `/usr/bin/env` as root without a password. `env` can launch a program, so this entry is effectively unrestricted command execution under root privileges:

```bash
sudo /usr/bin/env /bin/cat /root/root.txt
```

Result:

```text
PP{0p3r4t0r_3nv_1s_r00t::gXG9iPj_dtEE}
```

**Both flags were accepted by the competition platform.** The concrete chain was RSC deserialization → `nextjs` execution → local ops notes → Redis `session:operator` → bearer replay to internal API → `su operator` → NOPASSWD `/usr/bin/env` → root.

## 6. Defensive lessons

Patch Next.js/React RSC deserialization defects; isolate the app process from the operator's cache and administrative services; do not return reusable OS-account passwords through web APIs; scope tokens to services and identities; and never grant NOPASSWD sudo for general-purpose process launchers such as `env`. Input validation of the report fields cannot protect a deserialization path that runs before those checks.

[Sanitized evidence and timestamps](evidence/session-checkpoints.md)
