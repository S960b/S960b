#!/usr/bin/env python3
"""NAS coal / SunshineCTF 2026 - pull the Base64 payload out of the VBA macro.

Two modes:
  * a macro source file (e.g. assets/macro_MediaCache.bas) - the Base64 blob is
    taken straight from the `encoded = "..."` line;
  * an original macro-enabled Office file (.pptm/.docm/.xlsm) - olevba is called
    to decompile the module first (needs `pip install oletools`).

The blob is Base64 of a UTF-16LE string, which is what `powershell -EncodedCommand`
expects, so decoding with plain UTF-8 gives interleaved NUL bytes.

In the published artefacts the flag inside the payload is replaced by a mask of
the same length; run this against the original file to see the real one.
"""
import base64
import re
import shutil
import subprocess
import sys
from pathlib import Path

B64_IN_LINE = re.compile(r'encoded\s*=\s*"([A-Za-z0-9+/=]{40,})"')
B64_ANY = re.compile(r'[A-Za-z0-9+/=]{60,}')


def macro_source(path: Path) -> str:
    """Return the VBA source: read it directly, or ask olevba to decompile."""
    if path.suffix.lower() in {".bas", ".txt", ".vba"}:
        return path.read_text(errors="replace")

    cmd = None
    exe = shutil.which("olevba")
    if exe:
        cmd = [exe, str(path)]
    else:
        # olevba often lives in a venv/pipx prefix - try the module form too
        probe = subprocess.run([sys.executable, "-m", "oletools.olevba", "--help"],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            cmd = [sys.executable, "-m", "oletools.olevba", str(path)]
    if cmd is None:
        sys.exit("this looks like an Office file - install oletools (`pip install oletools`)\n"
                 "or dump the module first:  olevba -c '%s'\n"
                 "note: the public copy in assets/ has no macro by design - decode the\n"
                 "extracted module instead:  %s assets/macro_MediaCache.bas"
                 % (path, sys.argv[0]))
    print("[i] decompiling %s ..." % path, file=sys.stderr)
    out = subprocess.run(cmd, capture_output=True, text=True)
    if not out.stdout.strip():
        sys.exit("olevba produced no output: %s" % (out.stderr.strip()[:200] or "no macros?"))
    return out.stdout


def find_blob(source: str) -> str:
    m = B64_IN_LINE.search(source)
    if m:
        return m.group(1)
    cands = B64_ANY.findall(source)
    if not cands:
        sys.exit("no Base64 blob found - is this the macro module?")
    return max(cands, key=len)


def decode(blob: str) -> str:
    raw = base64.b64decode(blob)
    text = raw.decode("utf-16-le", errors="replace")
    if "\x00" in text:
        # not UTF-16LE after all; fall back to a lenient single-byte decode
        text = raw.decode("utf-8", errors="replace")
    return text


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        sys.exit("no such file: %s" % path)

    source = macro_source(path)
    blob = find_blob(source)
    print("[i] Base64 blob: %d chars" % len(blob))
    payload = decode(blob)

    print("[+] decoded payload (UTF-16LE -> text):\n")
    print(payload.rstrip())

    hits = re.findall(r"\$\w+\s*=\s*'([^']+)'", payload)
    if hits:
        print("\n[i] variables found: %s" % ", ".join(hits))
    flags = re.findall(r"\w+\{[^}]*\}", payload)
    if flags:
        print("[i] flag-shaped string in the payload: %s" % flags[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
