# Carbon: the portal, the RSC boundary and two recovered flags

[По-русски](solve.ru.md)

## 0. What the task asked for

Platypus Freight provided an emissions-report portal. The description said the operations team had cleaned the environment for SOC2 and removed stray passwords. The intended objectives were a user-level file at `/home/operator/user.txt` and a root-level file at `/root/root.txt`.

Both flags were recovered in the competition, but the exported CTF archive contains **no original Carbon image, exploit script, host history or final root-session transcript**. The evidence below is the preserved technical reconnaissance from the archived investigation notes, supplemented by the confirmed two-flag result. Where the record stops, the writeup stops rather than inventing a payload or privilege escalation.

## 1. First look: one exposed service

The initial reconnaissance reported a single exposed HTTP service, the portal on port 3000. Probes for TCP ports 22, 80, 443, 5432, 6379, 8080 and 9000 did not find accessible services.

The web application identified itself in the footer as:

```text
Carbon portal 2.4.1 · built with Next.js 15.5.6
```

The initial user interface at `/portal` exposed one functional action, **Request an emissions report**, implemented as the server action `queueEmissionsReport`. The corresponding serialized action identifier was recorded as a 40-hex-character value; it is intentionally omitted from the public artifact because it is deployment-specific.

The form accepted three fields:

| Field | Recorded server-side validation |
|---|---|
| `account` | `^PF-\d{6}$` |
| `quarter` | `^\d{4}-Q[1-4]$` |
| `kind` | one of `scope1`, `scope2`, `scope3`, `full` |

The initial note contains one accepted form example:

```text
Report CR-<id> queued for PF-123456 (2026-Q3, full).
You will get an e-mail once it is ready.
```

It also records rejection of obvious shell-style strings in `quarter` and ignored extra form parameters such as `is_admin` and `role`. These tests eliminated the simplest command-injection and mass-assignment hypotheses against the visible fields.

## 2. RSC / server-action behavior

The page's client chunk consisted of a `createServerReference(...)` call and the form, without a visible privileged REST API.

A direct JSON call to the server action, using `POST /portal`, `Next-Action` and `Content-Type: application/json`, returned an HTTP 500 with a React Server Components error digest:

```text
1:E{"digest":"1786758220"}
```

Form-data variants were also tested, but gave only digests rather than traces. That meant the action request was being processed, but it did not by itself establish code execution or a useful injection point.

Response headers provided two more observations:

```text
x-nextjs-prerender: 1
x-nextjs-cache: HIT
cache-control: s-maxage=31536000
```

The page was statically prerendered/cached. Other guessed paths, including `/admin`, `/api`, `/healthz` and `/internal`, returned 404. An `x-middleware-subrequest` test did not expose a bypass.

The important reasoning step was to stop treating the three form values as the only possible attack surface. They were validated. The framework's handling of serialized React Flight / RSC payloads is a different boundary, upstream of application input validation.

## 3. Framework hypothesis and its evidence level

The installed framework version was Next.js **15.5.6** with React 19. The earlier investigation identified the React Server Components deserialization class generally known as React2Shell (CVE-2025-55182) as the leading foothold hypothesis.

The original task asked for an exact multipart React Flight proof of concept, but that exact request is **not in the exported archive**. The following distinction is essential:

- **Observed:** the framework version, server action, validation rules, cached page, RSC error digest and reachable endpoint.
- **Recorded in the earlier reasoning:** the unauthenticated RSC deserialization route was selected for further validation.
- **Confirmed result, but without a surviving request transcript:** the team ultimately obtained both user and root flags.
- **Not established by the surviving material:** the exact exploit payload, its response, whether the successful foothold was the proposed CVE route, or the full root-escalation chain.

The two flag names, `c4rb0n_c4ch3d_s3ss10n_r3pl4y` and `0p3r4t0r_3nv_1s_r00t`, point toward a cached-session issue and operator-environment exposure. A flag name is a hint, **not** a substitute for an execution trace; it cannot establish exact token origin or privilege-escalation commands.

## 4. What was achieved

The final challenge report lists two flags, the user flag from the operator's home directory and a separate root flag. Both were reported accepted by the user after the competition.

The actual sequence of commands between initial foothold and these files is not present in `writeups/fullpwn_carbon`, which contains only a six-line summary in the original archive. No fabricated command log is inserted here.

## 5. Dead ends that were actually tested

- Direct field injection in `quarter` through semicolons, command substitution, backticks and pipe characters: rejected by strict regex.
- Invalid `kind`: rejected as unknown report type.
- Nonstandard extra form fields: ignored.
- Direct JSON action request: only a server-action digest, not a useful report or execution trace.
- Guessed admin/API/health routes: 404.
- `x-middleware-subrequest` header: no observed change.

These tests explain why framework internals became more interesting than the ordinary form fields.

## 6. Reproducibility boundary and mitigation

Without the original RSC payload and post-exploitation transcript, this cannot be described as a complete reproducible exploit. The final two-flag result is independently distinct from proving a particular CVE chain.

The practical lessons are to patch framework-level deserialization bugs regardless of strict application form validation, protect session caches from replay between security contexts, avoid exposing operator credentials or secrets to untrusted application processes and isolate root-owned resources from the web worker.

**Further evidence needed to close this writeup:** the original multipart request and the successful user-to-root terminal trace, if recoverable from the local session archives.
