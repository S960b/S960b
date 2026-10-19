#!/usr/bin/env python3
"""Passive local hook for authorized SOLARCTF UnrealDuck client.
Captures ClientReceivePayload(title FString, image TArray<uint8>) args.
Finds the module by name and does not crash if the probe address is not yet mapped.
Requires: py -m pip install frida  (run on the Windows PC running the game)
"""
import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

SCRIPT = r'''
'use strict';
const RV = 0x04394600;
const MOD_NAME = 'UnrealDuckClient-Win64-Shipping.exe';
function findMod() {
  for (const m of Process.enumerateModules()) {
    if (m.name.toLowerCase() === MOD_NAME.toLowerCase()) return m;
  }
  return null;
}
const mod = findMod();
if (!mod) {
  send({type:'ready', module:null, msg:'target module not loaded yet'});
} else {
  const addr = mod.base.add(RV);
  let sig = null, bad = null;
  try {
    sig = Array.from(new Uint8Array(addr.readByteArray(4)));
  } catch (e) { bad = String(e); }
  const expect = [0x40, 0x55, 0x56, 0x57];
  const ok = sig && sig.length === 4 && sig.every((v,i)=>v===expect[i]);
  send({type:'ready', module:mod.name, base:mod.base.toString(), handler:addr.toString(),
        signature: sig ? sig.join(',') : null, expected: expect.join(','), ok, bad});
  if (!ok) {
    send({type:'note', note:'probe mismatch (module may be a stub, none of listed match), still hooking'});
  }
  let hits = 0;
  Interceptor.attach(addr, {
    onEnter(args) {
      hits++;
      try {
        const title = args[1], arr = args[2];
        const titlePtr = title.readPointer();
        const tn = title.add(Process.pointerSize).readS32();
        let name = '';
        if (tn > 0 && tn <= 4096 && !titlePtr.isNull()) {
          name = titlePtr.readUtf16String(tn - 1);
        }
        const bytesPtr = arr.readPointer();
        const nn = arr.add(Process.pointerSize).readS32();
        const max = arr.add(Process.pointerSize + 4).readS32();
        const meta = {type:'payload',hit:hits,title:name,bytes:nn,max:max};
        if (nn < 0 || nn > (32*1024*1024) || max < nn || (nn && bytesPtr.isNull())) {
          meta.warning = 'invalid/oversized byte array (not saved)';
          send(meta);
          return;
        }
        send(meta, nn ? bytesPtr.readByteArray(nn) : new ArrayBuffer(0));
      } catch (e) { send({type:'error',hit:hits,error:String(e)}); }
    }
  });
}
'''


def guess_ext(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return '.png'
    if data.startswith(b'\xff\xd8\xff'): return '.jpg'
    if data.startswith(b'GIF87a') or data.startswith(b'GIF89a'): return '.gif'
    if data.startswith(b'BM'): return '.bmp'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP': return '.webp'
    return '.bin'


def clean(s):
    s = re.sub(r'[^a-zA-Z0-9_-]+', '_', s)[:48].strip('_')
    return s or 'untitled'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--process', default='UnrealDuckClient-Win64-Shipping.exe',
                   help='Windows process name or PID (fallback for attachment)')
    p.add_argument('--out', type=Path, default=Path('duck_payloads'))
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    try:
        import frida
    except ImportError:
        print('Install on the Windows PC: py -m pip install frida', file=sys.stderr)
        return 2

    def on_message(message, data):
        if message.get('type') == 'error':
            print('JS error:', message, flush=True)
            return
        m = message.get('payload', {})
        if m.get('type') == 'ready':
            print(f"Hook ready: {m.get('module')} base={m.get('base')} at={m.get('handler')} sig={m.get('signature')} ok={m.get('ok')} bad={m.get('bad')}", flush=True)
        elif m.get('type') == 'note':
            print('NOTE:', m.get('note'), flush=True)
        elif m.get('type') == 'error':
            print('Capture error:', m, flush=True)
        elif m.get('type') == 'payload':
            b = bytes(data or b'')
            print(f"Incoming ClientReceivePayload: title={m.get('title')!r} bytes={m.get('bytes')} captured={len(b)}", flush=True)
            tag = f"{int(time.time())}_{m['hit']:03d}_{clean(m.get('title',''))}_{hashlib.sha256(b).hexdigest()[:10]}"
            path = a.out / (tag + guess_ext(b))
            path.write_bytes(b)
            (a.out / (tag + '.json')).write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')
            print('Saved:', path, flush=True)

    try:
        target = int(a.process) if a.process.isdigit() else a.process
        session = frida.get_local_device().attach(target)
        script = session.create_script(SCRIPT)
        script.on('message', on_message)
        script.load()
        print('Attached. Waiting for server messages. Ctrl+C to stop.', flush=True)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('Stopped')
    except Exception as exc:
        print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())