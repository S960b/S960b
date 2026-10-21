#!/usr/bin/env python3
"""SOLARCTF UnrealDuck: auto-type verified route codes using ordinary Windows key events.
No memory writes or direct Unreal RPC calls. Only runs on Windows, against the user's
local Shipping client process. Stop with Ctrl+C.

Usage: py -m pip install frida
       py duck_auto_guide.py
"""
import ctypes
from ctypes import wintypes
import json
import os
import queue
import sys
import threading
import time

if os.name != 'nt':
    sys.exit('This script must run on the Windows PC where the game is running.')
try:
    import frida
except ImportError:
    sys.exit('Install Frida: py -m pip install frida')

TARGET = 'unrealduckclient-win64-shipping.exe'
CODES = {0: 'ILOVE', 1: 'DUCKS', 2: 'ALOTT'}

JS = r'''
'use strict';
const target = 'unrealduckclient-win64-shipping.exe';
const m = Process.enumerateModules().find(x => x.name.toLowerCase() === target);
if (!m) throw new Error('Shipping module not found; attached to wrong process');
function sendEvent(type, details) { send(Object.assign({type}, details || {})); }
function readable(p, n) {
  if (!p || p.isNull()) return false;
  const r = Process.findRangeByAddress(p);
  if (!r || r.protection.indexOf('r') < 0) return false;
  const end = Process.findRangeByAddress(p.add(n - 1));
  return end && end.protection.indexOf('r') >= 0;
}
const zone = m.base.add(0x4391670);
const signature = Array.from(new Uint8Array(zone.readVolatile(9)));
const expected = [0x4d,0x85,0xc0,0x0f,0x84,0xa2,0x01,0x00,0x00];
if (!signature.every((v,i) => v === expected[i]))
  throw new Error('RouteActivation signature mismatch; do not hook this build');
const last = {};
sendEvent('ready', {base:String(m.base), zone:String(zone)});

// Exact native code: r8 is overlapping Pawn; Pawn+0x2e8 -> Controller.
// RCX is route trigger object, [RCX+0x2c0] -> RouteId.
// At function exit, only accept confirmed activation on controller.
Interceptor.attach(zone, {
  onEnter(args) {
    this.pc = null;
    this.route = -1;
    try {
      const trigger = args[0], pawn = args[2];
      if (!readable(trigger.add(0x2c0), 1) || !readable(pawn.add(0x2e8), 8)) return;
      this.route = trigger.add(0x2c0).readU8();
      this.pc = pawn.add(0x2e8).readPointer();
    } catch (e) {}
  },
  onLeave() {
    try {
      if (!this.pc || this.pc.isNull() || this.route > 2 || this.route < 0) return;
      const pc = this.pc;
      if (!readable(pc.add(0x810), 4) || !readable(pc.add(0x7a8), 8)) return;
      const active = pc.add(0x7c8).readU8();
      const count = pc.add(0x810).readS32();
      const locked = pc.add(0x800).readU8();
      const profile = pc.add(0x7a8).readPointer();
      if (active !== this.route || profile.isNull() || count !== 0 || locked !== 0) return;
      const key = String(pc) + ':' + active;
      const now = Date.now();
      if (now - (last[key] || 0) < 4000) return;
      last[key] = now;
      sendEvent('route_started', {routeId:active, pc:String(pc), count, locked});
    } catch (e) { sendEvent('diagnostic', {message:'Route hook read: '+String(e)}); }
  }
});

// Observe normal input processing at FUNCTION ENTRY, not at its inner basic block.
// Avoids earlier crash caused by treating args[0] at 0x438f810 as PlayerController.
const prevInput = {};
Interceptor.attach(m.base.add(0x438f5f0), {
  onEnter(args) { this.pc = args[0]; },
  onLeave() {
    try {
      const pc = this.pc;
      if (!readable(pc.add(0x810), 4)) return;
      const id = pc.add(0x7c8).readU8();
      const n = pc.add(0x810).readS32();
      if (id > 2 || n < 0 || n > 5) return;
      const arr = pc.add(0x808).readPointer();
      if (n && !readable(arr, n)) return;
      const vals = n ? Array.from(new Uint8Array(arr.readVolatile(n))) : [];
      const text = vals.map(x => x < 26 ? String.fromCharCode(x+65) : '?').join('');
      const tag = String(pc) + ':' + id;
      if (prevInput[tag] === text) return;
      prevInput[tag] = text;
      sendEvent('input_state', {routeId:id, codesCount:n, codes:text});
    } catch(e) { sendEvent('diagnostic', {message:'Input watcher: '+String(e)}); }
  }
});

// Log actual codes sent to the server; observation only.
Interceptor.attach(m.base.add(0x4381d70), {
  onEnter(args) {
    try {
      const arr = args[2], id = args[1].toUInt32() & 255;
      if (!readable(arr, 16)) return;
      const p = arr.readPointer(), n = arr.add(8).readS32();
      if (n < 0 || n > 16 || (n && !readable(p, n))) return;
      const codes = n ? Array.from(new Uint8Array(p.readVolatile(n))) : [];
      sendEvent('route_sent', {routeId:id, codesCount:n,
        codes:codes.map(x => x < 26 ? String.fromCharCode(65+x) : '?').join('')});
    } catch(e) { sendEvent('diagnostic', {message:'RouteRPC read: '+String(e)}); }
  }
});
'''

# Win32 SendInput with the proper union layout on x64 (INPUT size = 40).
ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG),
                ('mouseData', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ULONG_PTR)]
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD),
                ('dwExtraInfo', ULONG_PTR)]
class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', wintypes.DWORD), ('wParamL', wintypes.WORD),
                ('wParamH', wintypes.WORD)]
class INPUTUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]
class INPUT(ctypes.Structure):
    _fields_ = [('type', wintypes.DWORD), ('u', INPUTUNION)]

user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

def foreground_pid():
    h = user32.GetForegroundWindow()
    result = wintypes.DWORD(0)
    if h: user32.GetWindowThreadProcessId(h, ctypes.byref(result))
    return result.value

def key_event(vk, up):
    inp = INPUT(type=1, u=INPUTUNION(ki=KEYBDINPUT(vk, 0, 2 if up else 0, 0, 0)))
    if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
        raise OSError(ctypes.get_last_error(), 'SendInput failed (check elevation/UIPI)')

def type_code(code, pid):
    for i, ch in enumerate(code):
        if foreground_pid() != pid:
            raise RuntimeError('Game lost keyboard focus; stopped before typing remaining characters')
        key_event(ord(ch), False)
        time.sleep(0.065)
        key_event(ord(ch), True)
        time.sleep(0.110)
        print(f'  [{i+1}/5] {ch}', flush=True)

def main():
    dev = frida.get_local_device()
    matches = [p for p in dev.enumerate_processes() if p.name.lower() == TARGET]
    if len(matches) != 1:
        sys.exit('Expected exactly one Shipping process, found: '+str([(p.pid,p.name) for p in matches]))
    pid = matches[0].pid
    events = queue.Queue()
    last_route = {}

    def on_message(message, data):
        if message.get('type') == 'error':
            print('FRIDA ERROR:', message.get('description'), flush=True)
            return
        m = message.get('payload', {})
        typ = m.get('type')
        if typ == 'route_started': events.put(m)
        else: print('FRIDA:', json.dumps(m, ensure_ascii=False), flush=True)

    def key_worker():
        while True:
            m = events.get()
            if m is None: return
            rid = m['routeId']
            now = time.monotonic()
            if rid not in CODES or now - last_route.get(rid, -9999) < 4:
                continue
            last_route[rid] = now
            code = CODES[rid]
            print(f'Route {rid} confirmed, scheduled {code}. Release WASD!', flush=True)
            time.sleep(0.40)
            if foreground_pid() != pid:
                print('SKIPPED: game not foreground; focus it and re-enter start zone', flush=True)
                continue
            try:
                type_code(code, pid)
                print('Keys injected normally. Move to route finish zone.', flush=True)
            except Exception as e:
                print('INPUT ERROR:', e, flush=True)

    print(f'Shipping PID={pid}. Type codes on activation: {CODES}.', flush=True)
    print('Keep game in FOREGROUND; do not hold WASD during automatic typing.', flush=True)
    t = threading.Thread(target=key_worker, daemon=True)
    t.start()
    session = dev.attach(pid)
    try:
        script = session.create_script(JS)
        script.on('message', on_message)
        script.load()
        print('READY: step onto a START zone. Ctrl+C in console to stop.', flush=True)
        while True: time.sleep(0.3)
    except KeyboardInterrupt:
        print('Stopped.', flush=True)
    finally:
        events.put(None)
        session.detach()

if __name__ == '__main__':
    main()
