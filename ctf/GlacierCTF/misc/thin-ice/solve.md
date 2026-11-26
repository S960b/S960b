# thin-ice — exploiting Git receive-pack as an object-content oracle

[Русская версия](solve.ru.md)

## 1. Setup and goal

The organizer's `thin_ice.tar.gz` includes a small Alpine `git-daemon` configuration. Its `entrypoint.sh` creates a working repository, writes the contents of `/flag.txt` into `flag.txt`, commits it as `test.` with fixed author/committer metadata and timestamp `2004-10-04T00:00:00Z`, and clones it into `/srv/git/shared.git`.

The daemon is started approximately as follows:

```sh
git daemon --reuseaddr --base-path=/srv/git --strict-paths --export-all \
  --listen=0.0.0.0 --port=1337 --enable=receive-pack \
  --disable=upload-pack --disable=upload-archive /srv/git/shared.git
```

The server also creates `hooks/update` with `exit 1`. So a normal clone cannot fetch the flag, while an ordinary push cannot update refs. The challenge is to retrieve the **contents** of an object in the repository solely through a push-side protocol interface.

The live launcher on TCP port `13372` required kCTF proof of work and, after ENTER, issued a 600-second instance. Its advertised Git address used a common router on port `13360`: `git://challs.glacierctf.com:13360/<uuid>--thin-ice/shared.git`.

## 2. First obstacle: routing to the actual Git process

Most initial probes returned the same generic error, `ERR access denied or repository not exported`. Testing alternative repository names, direct `git-upload-pack`, protocol versions and shell metacharacters did not help.

The key was a subtle **Git protocol v0 pkt-line**: the router requires a `host` field holding only the first 32 hexadecimal characters of the instance identifier, **not** the path suffix:

```text
git-receive-pack /<uuid>--thin-ice/shared.git\0host=<uuid>\0
```

The data is prefixed by its four-character hexadecimal pkt-line length. The path must match the issued path exactly. Even with correct routing, observed backend successes were only about one quarter of attempts, so the final transport retries up to 45 times.

With that request, `receive-pack` advertises existing refs, including:

```text
refs/heads/main = 2c7c30925427d63273b2fb14c7b81c6fe4197074
```

Thus the HEAD commit object ID is already known without fetching any object data. The author timestamp, committer timestamp, flag filename and commit message are fixed; the commit hash therefore depends deterministically on the secret flag.

## 3. Why a rejected push still matters

Locally, the investigation established that `git-receive-pack` can migrate objects out of its temporary quarantine during pack ingestion before the rejecting `update` hook prevents a ref update. This yields an object-writing primitive despite push failure. There is no ability to read objects directly through `upload-pack`.

The exploit uses **thin packs**: REF_DELTA entries may refer to an existing object as a base even though that base is absent from the received PACK payload. If the base object exists in the repository, Git can resolve the delta. If it does not exist, resolution fails.

A simple diagnostic also tests the object **length**: a delta's encoded `base_size` must match the existing base object's length. The reply differentiates a valid length (such as `unpack ok`) from an invalid one.

The earlier `duplicate base <oid>` diagnostic was usable in an older local test, but not reliable for the live Git 2.56.1. The successful route instead uses the number of unresolved deltas printed by `index-pack`.

## 4. The counted unresolved-delta oracle

Suppose a secret object `S` has known Git OID `H`, length `L`, Git type `kind` and we want the byte at offset `i`.

Choose a fresh random seven-byte nonce `r`. Construct a parent **REF_DELTA** with base `H`, transforming the secret object into:

```text
P = r || S[i]
```

The parent delta uses the existing secret object as its base, inserts the nonce, then uses a COPY instruction selecting one secret byte at offset `i`.

For every candidate byte `c`, predict the Git object identifier:

```text
candidate_oid = SHA1(kind + " " + len(r||c) + "\0" + r||c)
```

Add `w(c)` child REF_DELTAs that *reference* `candidate_oid` as base, without including that base in the pack. Each child would copy the 8-byte candidate and append `!`. The candidate objects are therefore mostly missing. Exactly one candidate — the actual secret byte — matches the derived parent `P`. Only that candidate's children can be resolved.

Use enough filler objects to make the pack contain **at least 110 entries** so the remote side selects `index-pack`, which exposes:

```text
pack has N unresolved deltas
```

If `T = sum(w(c))`, then:

```text
N = T - w(secret_byte)
secret_byte = inverse_weight(T - N)
```

Assign distinct positive weights for a small alphabet; for printable flag bytes, use ASCII 32..126, and for hexadecimal tree OIDs use `0123456789abcdef`. For arbitrary binary bytes (such as the tree's raw blob OID), use two requests: a high-nibble grouping where `w(c)=1+(c>>4)`, followed by a 16-value low-nibble request.

**Object type matters:** REF_DELTA preserves the base's type. Git hashes `commit`, `tree` and `blob` objects differently. Candidate hashes must therefore use the correct type (1, 2 or 3), not always `blob`.

## 5. Recovering the object chain

The working driver `run_flag.py` used the v2 counted oracle with checkpoints after each successfully recovered byte. Its exact sequence was:

| Phase | Starting OID | Bytes queried | How |
| --- | --- | ---: | --- |
| Recover tree SHA | advertised commit `2c7c3092…` | 40 | ASCII hex from commit body offset 5, commit length 166 |
| Recover blob SHA | tree `6fe60ef3…` | 20 | binary OID at tree body offset 16, length 36, two nibble queries per byte |
| Recover blob length | blob `5c5955cf…` | 16–27 | test delta `base_size=N`; first match at 27 |
| Recover flag bytes | same blob | 27 | printable ASCII counted oracle |

Complete recovered object IDs:

```text
commit 2c7c30925427d63273b2fb14c7b81c6fe4197074
tree   6fe60ef3d25dd3e8bccec929a4b44297e80e41a0
blob   5c5955cf03e044090e9db2f2280fe08acdb1c5e1
length 27 bytes
```

The path in the tree is `flag.txt`. Because a Git tree stores the blob OID as **20 raw bytes** after `100644 flag.txt\0`, its location is byte offset 16. The commit begins with `tree `, making its SHA hex field begin at byte 5.

A fresh nonce is important: otherwise previously ingested objects could create OID collisions that invalidate the counted oracle.

## 6. Successful remote run

The preserved `evidence/run_flag.log` shows successful PoW, an instance identifier, the 40/40 tree-hex reads, 20/20 raw blob-OID reads, length 27, and all 27/27 flag characters. It ends in `[+] FLAG` and `DONE` with the same answer. The corresponding `flag_state.json` contains the same HEAD/tree/blob/length/flag chain.

The agent recorded **118 Git requests in about 150 seconds** in one active instance. Instance lifetime was 600 seconds.

The participant separately confirmed that submitting the recovered flag was accepted by the GlacierCTF platform. In this public repository the value is redacted as:

```text
gctf{...}
```

## 7. An independent, offline hash verification

Git IDs can be verified independently of the network. The object hash is:

```text
SHA1("<type> <byte_length>\0" || raw_content)
```

In this challenge the flag file has no terminating newline (the entrypoint uses `printf '%s'`), and the one-entry tree is `100644 flag.txt\0` followed by the 20 raw bytes of the blob OID. The commit's fixed author, committer, date, message and tree line determine its exact 166-byte body.

The script [scripts/verify_git_chain.py](scripts/verify_git_chain.py) accepts the **private recovered flag** as an argument, recalculates all three hashes and asserts they equal those above. These values were recalculated offline while preparing this write-up and all match. The script does not contain the flag.

## 8. Materials and limitations

The agent's private archive `glacierctf_thin-ice_materials.zip` contains the original 2070-byte organizer tarball, unpacked handout, `scripts/run_flag.py`, `powlib.py`, `rlib.py`, `packutil.py`, `thin_ice_solver.py`, `thin_ice_solver_v2.py`, the failed `flagguess.py` approach, and the original success log and checkpoints.

This write-up gives the complete mechanism and a reproducible offline Git object check; the historical remote solver files are identified in [scripts/README.md](scripts/README.md). They are kept in the supplied evidence archive because they contain environment-specific paths and are not presented as newly re-run against an expired service.

The old chat documented local success for v2 but not yet a completed remote flag retrieval. The later agent bundle provides the actual successful remote transcript; these are distinct stages of the investigation, not conflicting outcomes.

## 9. Lessons

- Disabling fetch while permitting push does not guarantee that object contents are secret.
- Git protocol diagnostics and pack resolution can form a content oracle even when refs cannot be modified.
- Distinguish `upload-pack` from `receive-pack`; their preconditions and side channels differ.
- Count-based diagnostics permit recovering arbitrary bytes via weighted candidate multiplicities.
- Verify the recovered result against Git's content-addressed object hashes, not merely against a human-readable flag pattern.
