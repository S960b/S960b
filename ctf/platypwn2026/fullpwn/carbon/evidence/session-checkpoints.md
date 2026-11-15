# Carbon: confirmed steps from the November 14 session

Sanitized timeline; original live tokens, account passwords, host and instance addresses are not published.

- **00:06:** server-action multipart request to `/portal`, with `Next-Action: x`, `0`/ `1` Flight fields and a `NEXT_REDIRECT` output channel, returned HTTP **303**. Decoding `x-action-redirect` yielded `uid=1001(nextjs)`.
- **00:07:** direct `cat /home/operator/user.txt` as `nextjs` failed; environment inspection pointed to `/opt/carbon/ops-notes.md`.
- **00:08:** ops notes described unauthenticated loopback Redis `session:operator` and the bearer-token endpoint `/admin/operator-credential`.
- **00:09:** retrieved the operator password from the internal API; `su operator` in a pseudo-terminal succeeded and printed the first flag. `sudo -n -l` showed `(root) NOPASSWD: /usr/bin/env`.
- **00:10:** used the allowed `env` binary to execute a root-owned file read and obtained the second flag. Both submissions returned HTTP 200 with `{"status":1}`.

```text
USER PP{c4rb0n_c4ch3d_s3…Kt36U}
ROOT PP{0p3r4t0r_3nv_1s_…_dtEE}
```

The two accepted responses are direct challenge-platform confirmation, not just a local file read.
