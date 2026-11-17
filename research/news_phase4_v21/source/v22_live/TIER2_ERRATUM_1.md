# TIER-2 PROTOCOL — ERRATUM 1 (append-only correction)

```
erratum_id        tier2-erratum-1
created_utc       2026-11-17T12:40:00Z          (written now, UTC, verified)
corrects          TIER2_PROTOCOL_PREREGISTRATION.md
corrected_file_sha256_before_erratum
                  4555f83fc928f6ea6599121cdc82dff48150f01d19e40036dc9d587016c7d283
protocol_id       prospective-v2.5-tier2
owner_authorisation  task_id 760cfd0ff32f51a5c99ad18ef08613a5, seq 157
                     (27 staging trial approval; observational Tier-2 only)
```

## 1. What was wrong

The original file (commit `5b26ce3`) stated:

```
registered_utc   2026-11-17T09:52:00Z
```

That value was **wrong on two counts**:

1. **It was written by hand and was not derived from the commit.** It is a
   declared value, not evidence. I should have left the timestamp out of the
   authored body and let the git commit carry it.
2. **It is later than the commit that contains it.** Verified from git:

```
commit   5b26ce32856314d78f218d9182faab6a1fae94e7
author   2026-11-17T12:22:39+03:00
commit   2026-11-17T12:22:39+03:00
UTC      2026-11-17T09:22:39Z
```

A registration that claims a time after its own commit is either a typo or a
backdated claim. Here it is a typo, but the distinction cannot be settled by the
file itself, which is exactly why it must be corrected in the open rather than
quietly edited.

## 2. The verified facts

| item | value | how verified |
|---|---|---|
| first commit containing the protocol | `5b26ce3` | `git log` |
| that commit's UTC time | **2026-11-17T09:22:39Z** | `git log --format=%cI` |
| declared value in the original file | 2026-11-17T09:52:00Z | file, line 5 |
| discrepancy | declared is **29 min 21 s later** than the commit | arithmetic |
| Tier-2 rows existing at any point before this erratum | **0** | `~/.local/share/news-phase4-v25/` absent |
| model calls made under Tier-2 before this erratum | **0** | gate file never existed |

**The registration is still a valid BEFORE-DATA registration.** The correcting
fact is that the true registration time is the commit time
**2026-11-17T09:22:39Z**, not the hand-typed value, and no Tier-2 observation had
occurred at either moment. Nothing in the substantive protocol — claim scope,
separation from strict v2.2, refusal rules, analysis plan — is changed.

## 3. What this erratum does NOT do

- It does not rewrite the original file's history. The commit `5b26ce3` stands as
  published; its content is immutable evidence of what was claimed.
- It does not retroactively improve the registration's strength. A corrected
  timestamp does not make a sloppy registration rigorous.
- It does not authorize anything. Activation is a separate record that cites both
  this erratum's sha256 and the corrected protocol sha256.

## 4. Authoritative registration statement, from now on

```
protocol_id            prospective-v2.5-tier2
registered_utc         2026-11-17T09:22:39Z   (commit 5b26ce3, verified)
registration_basis     git commit timestamp, not an authored field
errata                 tier2-erratum-1 (this file)
observations_before_registration  0
```

Any future document that needs the registration time must use the commit-derived
value above and cite this erratum. The authored `registered_utc` line in the
original file is to be read as **superseded and factually incorrect**.
