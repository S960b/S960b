#!/usr/bin/env python3
# Remote client for the End of Life challenge.
#
# Sends base64(payload) as INPUT.TXT contents; the server runs it under
# DOSBox and prints OUTPUT.TXT (base64). Our payload makes the DOS program
# copy FLAG.TXT into OUTPUT.TXT, so the server reply contains the flag.
#
# Usage:
#   python3 solve.py [payload.INPUT] [host] [port]
import base64
import re
import socket
import ssl
import sys

HOST = 'end-of-life-<...>.inst20.ctf.fortid.com'
PORT = 1337


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
    payload = sys.argv[1] if len(sys.argv) > 1 else 'INPUT.TXT'
    host = sys.argv[2] if len(sys.argv) > 2 else HOST
    port = int(sys.argv[3]) if len(sys.argv) > 3 else PORT

    b64 = base64.b64encode(open(payload, 'rb').read())

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    raw = socket.create_connection((host, port), timeout=15)
    s = ctx.wrap_socket(raw, server_hostname=host)

    recv_until(s, b'base64): ')
    s.sendall(b64 + b'\n')

    out = b''
    s.settimeout(15)
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            out += chunk
    except socket.timeout:
        pass
    s.close()

    text = out.decode('utf-8', 'replace')
    print(text)
    m = re.search(r'Your OUTPUT\.TXT: ([A-Za-z0-9+/=]+)', text)
    if m:
        print()
        print('decoded OUTPUT.TXT:', base64.b64decode(m.group(1)).decode(errors='replace'))


if __name__ == '__main__':
    main()