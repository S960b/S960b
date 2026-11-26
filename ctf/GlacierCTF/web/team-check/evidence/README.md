# Team-Check evidence and status

The private agent archive `glacierctf_team-check_materials.zip` contains:

| File | What it proves |
| --- | --- |
| `REPORT.md` | Chronological on-instance tests, failed probes, RCE, flag reading and source recovery |
| `FLAG.txt` | Exact 70-byte accepted flag value, kept private |
| `scripts/ssti.py` | Historical exploitation helper |
| `evidence/ssti_49_response.html` | Original HTTP response to Jinja `{%print(7*7)%}`, displaying 49 |
| `evidence/tc_index.html` | Original 1,273-byte page used as baseline |
| `evidence/team_check_app_py_recovered_via_rce.txt` | Recovered `/app/app.py`; confirms literal blacklist and template interpolation |
| `assets/ctfd_challenge_33.json` | CTFd challenge metadata, not target application source |

The investigative transcript records `lipsum.__globals__['os'].popen('id').read()` returning `uid=0(root)`, then `cat /flag.txt` displaying the full gctf flag. The raw HTTP body from the flag-reading request was **not saved separately**, but its result was recorded in `FLAG.txt`.

**Accepted by platform:** the participant confirms acceptance of all solved flags submitted in this write-up series, including Team-Check. The historical agent report predates that confirmation.

The organizer's `team-check.tar.gz`, original `templates/index.html`, and platform acceptance screenshot are missing from the evidence bundle. No remote requests were performed during write-up publication. The full flag is masked in the public repository in keeping with the established convention.
