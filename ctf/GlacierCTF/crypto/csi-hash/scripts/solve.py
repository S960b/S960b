#!/usr/bin/env python3
"""CSI-HASH CTF: fast second-preimage solver.

The original PARI/LLL research proves that [-3]*32 is a relation in
Cl(Z[sqrt(-p)]).  For any 32 ASCII letters, subtracting 3 from each byte
produces a different, printable 32-byte message with the same hash.

Only connects when --connect is explicitly supplied.
"""
import argparse
import re
import socket

PATTERN = re.compile(rb"Here is your challenge: ([A-Za-z]{32})")

def preimage(challenge: bytes) -> bytes:
    if not re.fullmatch(rb"[A-Za-z]{32}", challenge):
        raise ValueError("Expected exactly 32 ASCII letters")
    result = bytes(b-3 for b in challenge)
    assert len(result)==32 and result != challenge
    assert all(33 <= b <= 126 for b in result)
    return result

def recv_prompt(sock):
    data = b""
    while b"> " not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Disconnected before prompt")
        data += chunk
    return data

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challenge", help="Offline transform of a given 32-letter challenge")
    parser.add_argument("--connect", action="store_true", help="Connect to historical CTF service")
    parser.add_argument("--host", default="challs.glacierctf.com")
    parser.add_argument("--port", type=int, default=13378)
    args=parser.parse_args()
    if args.challenge is not None:
        c=args.challenge.encode("ascii")
        print("challenge:",args.challenge)
        print("preimage: ",preimage(c).decode("ascii"))
    if args.connect:
        with socket.create_connection((args.host,args.port), timeout=20) as sock:
            sock.settimeout(220)
            greeting=recv_prompt(sock)
            match=PATTERN.search(greeting)
            if not match:
                raise RuntimeError("No 32-letter challenge: "+repr(greeting))
            answer=preimage(match.group(1))
            print("challenge:",match.group(1).decode())
            print("preimage: ",answer.decode())
            sock.sendall(answer+b"\n")
            output=b""
            while True:
                try:
                    buf=sock.recv(8192)
                except socket.timeout:
                    break
                if not buf: break
                output+=buf
            print("server accepted preimage:",b"Wow, you fund a preimage?" in output)
            print("flag returned:",bool(re.search(rb"gctf\{[^}\r\n]+\}",output)))
            # Do not disclose the real flag in a public write-up or script logs.

if __name__=="__main__":
    main()
