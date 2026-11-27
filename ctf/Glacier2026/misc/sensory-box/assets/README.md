# Original Sensory Box challenge materials

The provided agent bundle contains the organizer's unmodified `sensory_box.tar.gz` (4,710 bytes; SHA-256 `ee31dfc87aa812f7d87835b63cfad06c2700c5ed2033323089cd3f554857abd4`).

Inside `sensory_box/`:

| File | Role |
| --- | --- |
| `sensory_box.py` | game, hidden shell, age encryption, integrity check, archive generation |
| `Dockerfile` | nsjail and environment setup (`--disable_proc`, export of `FLAG`) |
| `entrypoint.sh` | launches the Python game |
| `flag.txt` | **dummy** organizer flag; not the live recovered secret |
| `deploy.sh`, `deploy.bat` | local deployment |
| `sha256sum` | checksums of handout files |

The source lines of interest are `FLAG = os.getenv("FLAG", ...)`, `subprocess.Popen(["/bin/sh"], ...)`, `get_boxes_content()`, the final `get_boxes_content()` comparison, and `subprocess.check_output(["tar", "cJ"] + files)`.

Large or private artifacts are not duplicated into the repository; use the original bundle for full inspection.
