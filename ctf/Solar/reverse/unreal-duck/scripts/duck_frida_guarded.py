#!/usr/bin/env python3
"""Frida guarded local diagnostic + optional payload capture for UnrealDuck.

This tool only attaches to the exact Windows UnrealDuck Shipping process.
It checks code signature & executable memory permissions before hooking.
Run from same directory as duck_frida_guarded.js.
"""
import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

TARGET = 'unrealduckclient-win64-shipping.exe'
EXPECTED_SHA256 = 'd0aff10fbd7e980dc9edcaa8cc95d232a37b11c7eb977ad9e66784cec532b802'
EXPECTED_SIZE = 143861760

def guess_extension(b):
    if b.startswith(b'\x89PNG\r\n\x1a\n'): return '.png'
    if b.startswith(b'\xff\xd8\xff'): return '.jpg'
    if b.startswith(b'RIFF') and b[8:12] == b'WEBP': return '.webp'
    if b.startswith((b'GIF87a', b'GIF89a')): return '.gif'
    if b.startswith(b'BM'): return '.bmp'
    return '.bin'

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pid', type=int, help='PID of real UnrealDuckClient-Win64-Shipping.exe')
    ap.add_argument('--exe', type=Path, help='Path to the EXACT on-disk Shipping EXE for SHA-256 check')
    ap.add_argument('--scan', action='store_true', help='If fixed RVA fails, scan this module for the full 23-byte code signature')
    ap.add_argument('--out', type=Path, default=Path('duck_payloads'))
    args = ap.parse_args()
    if args.exe is not None:
        if not args.exe.is_file():
            ap.error(f'EXE missing: {args.exe}')
        h = hashlib.sha256()
        with args.exe.open('rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                h.update(chunk)
        size = args.exe.stat().st_size
        print(f'On-disk EXE: {args.exe}\nsize={size} sha256={h.hexdigest()}', flush=True)
        if size != EXPECTED_SIZE or h.hexdigest() != EXPECTED_SHA256:
            print('WARNING: EXE differs from reversed sample; hardcoded RVA is unverified.', flush=True)
            if not args.scan:
                print('Diagnostic mode only: the JS will refuse to hook on signature mismatch. Use --scan only if necessary.', flush=True)
        else:
            print('Byte-identical to reverse-engineered EXE.', flush=True)
    try:
        import frida
    except ImportError:
        sys.exit('Please install frida: py -m pip install frida')
    device = frida.get_local_device()
    try:
        processes = [p for p in device.enumerate_processes() if p.name.lower() == TARGET]
        print('Matching Shipping processes:', [(p.pid, p.name) for p in processes], flush=True)
        if args.pid is None:
            if len(processes) != 1:
                sys.exit('Specify --pid: zero or multiple Shipping processes found; NOT attaching to launcher.')
            pid = processes[0].pid
        else:
            pid = args.pid
            if not any(p.pid == pid for p in processes):
                sys.exit(f'PID {pid} is not an exact-name Shipping process. Refusing to attach.')
    except Exception as e:
        sys.exit(f'Cannot enumerate target: {e}')
    jsfile = Path(__file__).with_name('duck_frida_guarded.js')
    if not jsfile.is_file(): sys.exit(f'JS file missing: {jsfile}')
    source = jsfile.read_text(encoding='utf-8')
    if args.scan:
        source = source.replace('const SCAN_FALLBACK = false;', 'const SCAN_FALLBACK = true;')
    args.out.mkdir(parents=True, exist_ok=True)
    count = 0
    def on_message(message, data):
        nonlocal count
        if message.get('type') == 'error':
            print('FRIDA SCRIPT ERROR:', message.get('description'), message.get('stack', ''), flush=True)
            return
        m = message.get('payload', {})
        t = m.get('type')
        if t == 'payload':
            count += 1
            b = bytes(data or b'')
            title = m.get('title', '')
            safe = re.sub(r'[^a-zA-Z0-9_-]', '_', title)[:40] or 'untitled'
            prefix = f'{int(time.time())}_{count:04d}_{safe}_{hashlib.sha256(b).hexdigest()[:10]}'
            p = args.out / (prefix + guess_extension(b))
            p.write_bytes(b)
            (args.out / (prefix + '.json')).write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'PAYLOAD #{count}: title={title!r}, bytes={len(b)}, saved={p}', flush=True)
        else:
            print(json.dumps(m, ensure_ascii=False, default=str), flush=True)
    session = None
    try:
        print('Attaching to pid', pid, flush=True)
        session = device.attach(pid)
        script = session.create_script(source)
        script.on('message', on_message)
        script.load()
        print('Watching for diagnostics / payload. Ctrl+C to stop.', flush=True)
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print('Stopped by user', flush=True)
    except Exception as e:
        print('Frida attach/runtime error:', repr(e), file=sys.stderr)
        return 1
    finally:
        if session is not None:
            try: session.detach()
            except Exception: pass
    return 0

if __name__ == '__main__':
    sys.exit(main())
