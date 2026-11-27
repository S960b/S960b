#!/usr/bin/env python3
"""Inspect the Frostgate License Manager original handout without executing it.

python3 inspect_handout.py 'virt.tar.gz'
python3 inspect_handout.py 'virt.exe'
"""
import argparse
import hashlib
import io
import pathlib
import struct
import tarfile

EXPECTED_EXE_SHA256 = "ec5620b986ee7102054e676998dedcea7e25f101b0def8d7366f83010bf16ce2"

def inspect_binary(data):
    print("file size:", len(data))
    got = hashlib.sha256(data).hexdigest()
    print("SHA-256:", got)
    print("organizer checksum matches:", got == EXPECTED_EXE_SHA256)
    assert data[:2] == b"MZ", "Not a PE file"
    peoff = struct.unpack_from("<I", data, 0x3c)[0]
    assert data[peoff:peoff+4] == b"PE\x00\x00"
    machine, nsections = struct.unpack_from("<HH", data, peoff+4)
    opts = peoff+24
    size = struct.unpack_from("<H",data,peoff+20)[0]
    magic = struct.unpack_from("<H",data,opts)[0]
    entry = struct.unpack_from("<I",data,opts+16)[0]
    imagebase = struct.unpack_from("<Q",data,opts+24)[0] if magic==0x20b else None
    print("PE machine:",hex(machine),"sections:",nsections)
    print("PE magic:",hex(magic),"entry RVA:",hex(entry),"image base:",hex(imagebase or 0))
    assert machine==0x8664 and magic==0x20b
    for i in range(nsections):
        off=opts+size+40*i
        name=data[off:off+8].split(b"\x00",1)[0].decode("ascii","replace")
        vsize,va,rsize,raw=struct.unpack_from("<IIII",data,off+8)
        print("section",name,"VA",hex(va),"virtual size",hex(vsize),
              "raw offset",hex(raw),"raw size",hex(rsize))
    for token in [b"Frostgate License Manager",b"Enter license key:",b"Checking license"]:
        print(repr(token.decode()),"found:",token in data)
    print("plaintext flag-like markers:",b"gctf{" in data,b"wrong{" in data)
    print("No Windows code executed; this is NOT a license solver.")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("path",help="Original virt.tar.gz archive or extracted virt.exe")
    args=p.parse_args()
    path=pathlib.Path(args.path)
    if path.suffix==".exe":
        data=path.read_bytes()
    else:
        with tarfile.open(path,"r:gz") as tar:
            member=tar.getmember("virt/virt.exe")
            data=tar.extractfile(member).read()
            checksum=tar.extractfile(tar.getmember("virt/sha256sum")).read().decode().strip()
            print("handout sha256sum:",checksum)
    inspect_binary(data)

if __name__=="__main__":
    main()
