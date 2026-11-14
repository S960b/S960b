#!/usr/bin/env python3
"""
DEStruction (3DES-GCM/64, 16-bit tag) CTF exploit.

Usage:
  python3 destruction_solve.py CHALLENGE_HOST CHALLENGE_PORT
  python3 destruction_solve.py --offline response.txt

No third-party modules required. On a real server, the first encryption request
is for a 12-byte chosen plaintext and 2^20 bytes of associated-data padding.
The two verification submissions recover FLAG0 with certainty (except transport
errors). The full GHASH-difference is in the bottom 49 bits, so the tag's
first 15 bits don't change; the last tag bit has two possible values.
"""
import argparse
import re
import socket
from pathlib import Path

AD_REQUEST_LENGTH = 1 << 20
CHOSEN_PLAINTEXT = b"ABCDEFGHflag"
TARGET_PLAINTEXT = b"give me flag"

# Coefficients obtained by solving a 960x960 GF(2) linear system.
# For any H in GF(2^64), top15((ABCDEFGH xor 'give me ') * H^4
#   xor sum(coeff[e] * H^e)) = 0, with GHASH field multiplication.
# Exponents are all powers of 2. Their terms fit into the 131073-block AD.
AD_COEFFS = {
    8:      0xec300472f4f6ff41,
    16:     0x67805a96c1d9bd25,
    32:     0xa261dd3f864d6fba,
    64:     0x311830c9f3917f41,
    128:    0xf44ed25fa0552705,
    256:    0x61cca04522ab7ffd,
    512:    0x4efd0f3a2fae2d00,
    1024:   0xa032343714be6d2c,
    2048:   0x23294f7a59fd0804,
    4096:   0xd5801b14f6840498,
    8192:   0x1e790f09679a4a3a,
    16384:  0xf312fe22d24bec1d,
    32768:  0xa1f325084cc1c801,
    65536:  0xd90c11dbdd147845,
    131072: 0x2692e1a65cf2c3bb,
}


def parse_encryption_response(output):
    if isinstance(output, str):
        output = output.encode('ascii')
    marker = b'Here is a result: '
    if marker in output:
        output = output.split(marker, 1)[1]
    line = output.split(b'\n', 1)[0].strip()
    try:
        nonce, ciphertext, ad, tag = [bytes.fromhex(p.decode()) for p in line.split(b',')]
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError('Expected: nonce,ciphertext,associated_data,tag') from exc
    if len(ciphertext) != 12 or len(tag) != 2 or len(ad) != AD_REQUEST_LENGTH + 4:
        raise ValueError(
            f'Unexpected sizes: ciphertext={len(ciphertext)}, '
            f'AD={len(ad)}, tag={len(tag)}; need 12,{AD_REQUEST_LENGTH+4},2'
        )
    return nonce, ciphertext, ad, tag


def make_payloads(output):
    nonce, ct, ad, tag = parse_encryption_response(output)

    # Recover original CTR ciphertext relation without knowing the key.
    # Only the first 8 bytes change; 'flag' already occupies the last 4.
    delta = bytes(x ^ y for x, y in zip(CHOSEN_PLAINTEXT, TARGET_PLAINTEXT))
    assert delta[8:] == b'\0'*4
    new_ct = bytes(c ^ d for c, d in zip(ct, delta))

    new_ad = bytearray(ad)
    m = (len(ad) + 7)//8
    for exponent, coef in AD_COEFFS.items():
        # In GHASH(A,C) with 2 ciphertext blocks, AD[i] has exponent m+4-i.
        block_i = m + 4 - exponent
        off = block_i * 8
        assert 0 <= off and off + 8 <= len(ad)
        change = coef.to_bytes(8, 'big')
        for j in range(8):
            new_ad[off+j] ^= change[j]
    new_ad = bytes(new_ad)

    # The new tag is either unchanged, or changed by bit 0 of byte 2.
    tags = (tag, tag[:-1] + bytes([tag[-1] ^ 1]))
    return [b','.join((nonce.hex().encode(), new_ct.hex().encode(),
                       new_ad.hex().encode(), t.hex().encode())) for t in tags]


class Connection:
    def __init__(self, host, port, timeout=120):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buffer = bytearray()
    def until(self, needle):
        if isinstance(needle, str): needle = needle.encode()
        while True:
            idx = self.buffer.find(needle)
            if idx >= 0:
                end = idx + len(needle)
                data = bytes(self.buffer[:end])
                del self.buffer[:end]
                return data
            chunk = self.sock.recv(1<<18)
            if not chunk:
                raise ConnectionError('Server closed connection before ' + repr(needle))
            self.buffer.extend(chunk)
    def line(self, data):
        self.sock.sendall(data + b'\n')
    def close(self):
        self.sock.close()


def solve_remote(host, port):
    conn = Connection(host, port)
    try:
        conn.until('Enter your choice: ')
        conn.line(b'1')
        conn.until('Enter AD length: ')
        conn.line(str(AD_REQUEST_LENGTH).encode())
        conn.until('plain' + 'text in hex: ')
        conn.line(CHOSEN_PLAINTEXT.hex().encode())
        data = conn.until('Enter your choice: ')
        payloads = make_payloads(data)
        print('[+] Encryption result received; two forgery candidates prepared.', flush=True)
        for i, payload in enumerate(payloads, 1):
            conn.line(b'2')
            conn.until('Submit your ciphertext in hex: ')
            print(f'[*] Trying forged tag {i}/2 ({len(payload)} hex bytes).', flush=True)
            conn.line(payload)
            result = conn.until('Enter your choice: ')
            match = re.search(rb'Congratulations! Here is flag:\s*([0-9a-fA-F]+)', result)
            if match:
                flag = bytes.fromhex(match.group(1).decode())
                print(f'[+] FLAG0: {flag.decode("utf-8", "replace")}', flush=True)
                return flag
            print('[*] That tag was rejected.', flush=True)
        raise RuntimeError('Both tags rejected; unexpected challenge version or server bug')
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser(description='Universal 16-bit GHASH tag forgery for DEStruction')
    p.add_argument('host', nargs='?', help='CTF server hostname')
    p.add_argument('port', nargs='?', type=int, help='CTF server port')
    p.add_argument('--offline', type=Path, help='File containing one "Here is a result: ..." response line')
    args = p.parse_args()
    if args.offline is not None:
        payloads = make_payloads(args.offline.read_bytes())
        for i, payload in enumerate(payloads):
            fname = Path(f'destruction_forgery_{i}.txt')
            fname.write_bytes(payload + b'\n')
            print(f'[+] {fname} ({len(payload):,} bytes)')
    elif args.host and args.port:
        solve_remote(args.host, args.port)
    else:
        p.error('Supply HOST PORT, or --offline response.txt')

if __name__ == '__main__':
    main()
