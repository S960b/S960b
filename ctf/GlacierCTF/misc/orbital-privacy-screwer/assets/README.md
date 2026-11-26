# Organizer's original files

The agent's `glacierctf_orbital-privacy-screwer_materials.zip` contains the byte-preserved `assets/orbital-privacy-screwer.tar.gz` (15,025 bytes) plus extracted files:

- `Dockerfile` — Alpine 3.21.3 and Python 3;
- `entrypoint.sh` — starts `src/server.py` on port 1337;
- `src/server.py` — SSE acquisition endpoint, Morse encoder, cancellation and diagnostic hashes;
- `src/static/index.html`, `css/dashboard.css`, `js/app.js`, `js/map.js`;
- `flag.txt` — dummy `gctffakefakefake`, **not** the live secret;
- deploy scripts and organizer `sha256sum`.

The background map image was absent from the original tarball but was downloaded from the live endpoint and preserved as `evidence/static_snapshot/background-image.png`; the evidence bundle also contains two cropped/contrast-adjusted inspection images.

Important implementation detail: `precise_sleep()` combines `time.sleep` with a `perf_counter` busy wait for more accurate short delays. In `handle_acquire_stream()`, it runs **after** each SSE step and before the following event, which creates the intentional timing channel.

The original assets and images are held in the private archive and are not redistributed here.
