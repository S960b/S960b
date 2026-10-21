#!/usr/bin/env python3
"""Search extracted UE files for SOLARCTF flags and hints in ASCII and UTF-16LE.
Streams in blocks and overlaps boundaries; does not need to load files into RAM.
Usage: python3 duck_scan.py <directory> [<directory> ...]
"""
import os
import re
import sys

ASCII_FLAG = re.compile(rb'SOLARCTF\{[\x20-\x7e]{1,180}?\}', re.I)
UTF16_FLAG = re.compile(re.escape('SOLARCTF{'.encode('utf-16le')) + rb'(?:[\x20-\x7e]\x00){1,180}?\}\x00', re.I)
ASCII_HINT = re.compile(rb'(?i:SOLARCTF|flag|secret|password|keycode|duck)', re.I)
UTF16_HINT = re.compile(rb'(?i:S\x00O\x00L\x00A\x00R\x00C\x00T\x00F\x00|f\x00l\x00a\x00g\x00|s\x00e\x00c\x00r\x00e\x00t\x00)', re.I)
BLOCK, OVERLAP = 1 << 20, 512

def scan_file(filename):
    found_flags = set()
    hints = set()
    try:
        with open(filename, 'rb') as f:
            carry = b''
            pos = 0
            while data := f.read(BLOCK):
                buf = carry + data
                start = pos - len(carry)
                for patt, encoding in ((ASCII_FLAG, 'ascii'), (UTF16_FLAG, 'utf-16le')):
                    for m in patt.finditer(buf):
                        offset = start + m.start()
                        if (offset, encoding) in found_flags:
                            continue
                        found_flags.add((offset, encoding))
                        print(f'FLAG {filename}:0x{offset:x} [{encoding}] {m.group().decode(encoding, errors="replace")}', flush=True)
                if len(hints) < 6:
                    for patt, encoding in ((ASCII_HINT, 'ascii'), (UTF16_HINT, 'utf-16le')):
                        for m in patt.finditer(buf):
                            offset = start + m.start()
                            if offset < pos - len(carry) or offset in hints:
                                continue
                            hints.add(offset)
                            if len(hints) >= 6:
                                break
                pos += len(data)
                carry = buf[-OVERLAP:]
    except (PermissionError, OSError) as exc:
        print(f'WARN {filename}: {exc}', file=sys.stderr)
        return
    if hints and not found_flags:
        print(f'HINT {filename}: {len(hints)} keyword positions (capped at 6)')

def main(paths):
    for src in paths:
        if os.path.isfile(src):
            scan_file(src)
        elif os.path.isdir(src):
            for root, dirs, files in os.walk(src):
                dirs.sort()
                for name in sorted(files):
                    scan_file(os.path.join(root, name))
        else:
            print(f'WARN path not found: {src}', file=sys.stderr)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python3 duck_scan.py directory [directory ...]')
    main(sys.argv[1:])
