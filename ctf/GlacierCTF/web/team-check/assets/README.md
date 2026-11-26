# Organizer materials and provenance

The participant supplied `glacierctf_team-check_materials.zip`.

- `assets/ctfd_challenge_33.json`: CTFd API response for challenge ID 33, including title, `Beginnerfriendly` tag, description and the download link to `team-check.tar.gz`. The preserved snapshot listed a dynamic **500-point** value at the start of CTF; the current challenge card is **50 points**. These are different scoring snapshots, not different challenges.
- Original **`team-check.tar.gz` was NOT downloaded**: the CTF file endpoint returned HTTP 502 in the historical investigation. This archive therefore does not contain organizer code or deployment files.
- `evidence/team_check_app_py_recovered_via_rce.txt`: **application source read from the running container using the SSTI RCE**. It is a transcript copy, not a byte-verified organizer source file. The list of team names might have transcription errors.
- `evidence/tc_index.html`: captured index HTML (1,273 bytes).
- `evidence/ssti_49_response.html`: captured GET response to `{%print(7*7)%}` (1,273 bytes).
- `scripts/ssti.py`: retained original script, using only Python stdlib.

The historical recovered `app.py` calls `serve(app, host='0.0.0.0', port=1337)` via Waitress. It contains the vulnerable conditional and `flask.render_template_string(f"...{team_name}...")`.

The original Jinja template file `templates/index.html` was not saved. A locally recreated template would be only a reconstruction, not an original artifact.
