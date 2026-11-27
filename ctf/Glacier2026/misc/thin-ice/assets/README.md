# Original handout and service configuration

The private evidence archive `glacierctf_thin-ice_materials.zip` includes the original organizer's `assets/thin_ice.tar.gz` (2070 bytes) and unchanged extracted files in `assets/handout/`.

| File | Purpose |
| --- | --- |
| `Dockerfile` | pinned Alpine image; installs `git-daemon`; exposes TCP 1337 |
| `entrypoint.sh` | creates repository, deterministic commit containing flag; rejects ref updates; starts Git daemon |
| `deploy.sh` and `deploy.bat` | launch scripts for Linux/Windows |
| `sha256sum` | organizer-provided checksums |
| `flag.txt` | organizer's **dummy flag** (not the competition flag) |

Important service settings from `entrypoint.sh`:

```sh
git init --quiet --initial-branch=main "$worktree"
# populate flag.txt and commit "test." with fixed metadata
git clone --quiet --bare "$worktree" "$repo"
printf '#!/bin/sh\nexit 1\n' > "$repo/hooks/update"
chmod 0555 "$repo/hooks/update"
git daemon --base-path=/srv/git --strict-paths --export-all \
  --enable=receive-pack --disable=upload-pack \
  --disable=upload-archive "$repo"
```

The initial commit metadata is fixed to `losfuzzys <mail@losfuzzys.net>`, timestamp `2004-10-04T00:00:00Z`, message `test.`. The saved handout's `sha256sum -c sha256sum` check reportedly passed for all five files.

The 27-byte recovered value produces the same remote commit/tree/blob identities; see the offline verifier.
