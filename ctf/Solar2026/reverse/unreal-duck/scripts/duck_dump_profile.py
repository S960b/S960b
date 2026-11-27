#!/usr/bin/env python3
"""Dump the native DuckRouteProfileAsset from the running UnrealDuck Shipping client.

Hooks RouteActivation (RVA 0x4391670) and the ServerRecordFrame sender (RVA 0x4381D70),
follows Pawn+0x2E8 to the controller, reads PC+0x7A8 as the profile pointer and prints
the header constants and the records in readable form.

Usage: py -m pip install frida
       py duck_dump_profile.py --pid 1234 --out profile_dump.json
"""
import argparse
import json
import sys

TARGET = 'unrealduckclient-win64-shipping.exe'
RECORD_STRIDE = 0x1C

EXPECTED_KEY = 0x2F6CB09A
EXPECTED_38 = 0x2972E958
EXPECTED_54 = 0x2FD2EEE2

EXPECTED_RECORDS = [
    {'route': 0, 'a': 0x6F37AA0B, 'b': 0x112BB232, 'c': 0x29B1711A,
     'd': 0x45D39389, 'e': 0xB2DCBF59, 'f': 0x6F4B4EDA},
    {'route': 1, 'a': 0x17D98198, 'b': 0x42413596, 'c': 0x73257244,
     'd': 0x57D52250, 'e': 0x2523AB39, 'f': 0x203585FC},
    {'route': 2, 'a': 0x2F48611F, 'b': 0x39C19D54, 'c': 0x158B4D73,
     'd': 0x583A99D9, 'e': 0xB01E4F4D, 'f': 0x2B74299A},
]

JS = r'''
'use strict';
const target = 'unrealduckclient-win64-shipping.exe';
const m = Process.enumerateModules().find(x => x.name.toLowerCase() === target);
if (!m) throw new Error('Shipping module not found; attached to wrong process');

function readable(p, n) {
  if (!p || p.isNull()) return false;
  const r = Process.findRangeByAddress(p);
  if (!r || r.protection.indexOf('r') < 0) return false;
  const end = Process.findRangeByAddress(p.add(n - 1));
  return end && end.protection.indexOf('r') >= 0;
}

function checkSignature(addr, expected) {
  const got = Array.from(new Uint8Array(addr.readVolatile(expected.length)));
  return got.every((v, i) => v === expected[i]);
}

const ACTIVATION = m.base.add(0x4391670);
if (!checkSignature(ACTIVATION, [0x4d,0x85,0xc0,0x0f,0x84,0xa2,0x01,0x00,0x00]))
  throw new Error('RouteActivation signature mismatch; refusing to hook');

const FRAME_SENDER = m.base.add(0x4381d70);
if (!checkSignature(FRAME_SENDER, [0x4c,0x8b,0xdc,0x48,0x83,0xec,0x68,0x33,0xc0]))
  throw new Error('ServerRecordFrame sender signature mismatch; refusing to hook');

function readProfile(profile) {
  if (profile.isNull() || !readable(profile.add(0x58), 1)) return null;
  const arr = profile.add(0x40).readPointer();
  const n = profile.add(0x48).readS32();
  const header = {
    seed_38: profile.add(0x38).readU32(),
    records_ptr: arr.toString(),
    records_num: n,
    key_50: profile.add(0x50).readU32(),
    extra_54: profile.add(0x54).readU32()
  };
  const records = [];
  const count = Math.min(Math.max(n, 0), 8);
  for (let i = 0; i < count; i++) {
    if (!readable(arr.add(i * 0x1c), 0x1c)) break;
    const r = arr.add(i * 0x1c);
    records.push({
      i: i,
      route: r.add(0x04).readU32(),
      a: r.add(0x00).readU32(),
      b: r.add(0x08).readU32(),
      c: r.add(0x0c).readU32(),
      d: r.add(0x10).readU32(),
      e: r.add(0x14).readU32(),
      f: r.add(0x18).readU32()
    });
  }
  return {header: header, records: records};
}

function fromController(pc, source) {
  if (pc.isNull() || !readable(pc.add(0x7a8), 8)) return;
  const profile = pc.add(0x7a8).readPointer();
  const data = readProfile(profile);
  if (!data) return;
  send({
    type: 'profile',
    source: source,
    controller: pc.toString(),
    profile: profile.toString(),
    header: data.header,
    records: data.records
  });
}

Interceptor.attach(ACTIVATION, {
  onEnter: function (args) {
    try {
      const pawn = args[2];
      if (pawn.isNull() || !readable(pawn.add(0x2e8), 8)) return;
      fromController(pawn.add(0x2e8).readPointer(), 'activation_pawn');
    } catch (e) {
      send({type: 'error', message: String(e)});
    }
  }
});

Interceptor.attach(FRAME_SENDER, {
  onEnter: function (args) {
    try {
      fromController(args[0], 'frame_sender');
    } catch (e) {
      send({type: 'error', message: String(e)});
    }
  }
});

send({type: 'ready', base: m.base.toString()});
'''


def compare(dump):
    problems = []
    header = dump['header']
    if header['key_50'] != EXPECTED_KEY:
        problems.append(f"key_50 {header['key_50']:#010x} != {EXPECTED_KEY:#010x}")
    if header['seed_38'] != EXPECTED_38:
        problems.append(f"seed_38 {header['seed_38']:#010x} != {EXPECTED_38:#010x}")
    if header['extra_54'] != EXPECTED_54:
        problems.append(f"extra_54 {header['extra_54']:#010x} != {EXPECTED_54:#010x}")
    if header['records_num'] != len(EXPECTED_RECORDS):
        problems.append(f"records_num {header['records_num']} != {len(EXPECTED_RECORDS)}")
    for rec, exp in zip(dump['records'], EXPECTED_RECORDS):
        for field, value in exp.items():
            got = rec['route'] if field == 'route' else rec[field]
            if got != value:
                problems.append(f"rec{rec['i']}.{field} {got:#010x} != {value:#010x}")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pid', type=int, help='PID of the Shipping client process')
    ap.add_argument('--out', default='profile_dump.json', help='where to write the JSON dump')
    args = ap.parse_args()
    try:
        import frida
    except ImportError:
        sys.exit('Install Frida: py -m pip install frida')

    device = frida.get_local_device()
    pid = args.pid
    if pid is None:
        found = [p for p in device.enumerate_processes() if p.name.lower() == TARGET]
        if len(found) != 1:
            sys.exit(f'Found {len(found)} Shipping processes, specify --pid')
        pid = found[0].pid

    session = device.attach(pid)
    script = session.create_script(JS)
    saved = {'dumps': []}

    def on_message(message, data):
        if message['type'] == 'error':
            print('FRIDA ERROR:', message.get('description'), flush=True)
            return
        payload = message.get('payload') or {}
        kind = payload.get('type')
        if kind == 'ready':
            print(f"attached, module base {payload['base']}", flush=True)
        elif kind == 'error':
            print('JS ERROR:', payload.get('message'), flush=True)
        elif kind == 'profile':
            problems = compare(payload)
            status = 'MATCH' if not problems else 'MISMATCH'
            print(f"dump source={payload['source']} controller={payload['controller']} "
                  f"profile={payload['profile']} {status}", flush=True)
            print(json.dumps(payload, indent=2), flush=True)
            for p in problems:
                print('  difference:', p, flush=True)
            if payload not in saved['dumps']:
                saved['dumps'].append(payload)
                with open(args.out, 'w', encoding='utf-8') as f:
                    json.dump(saved, f, indent=2)

    script.on('message', on_message)
    script.load()
    print('Waiting for a route trigger. Enter the start zone in the game. Ctrl+C to stop.', flush=True)
    try:
        sys.stdin.read()
    except KeyboardInterrupt:
        pass
    finally:
        session.detach()


if __name__ == '__main__':
    main()
