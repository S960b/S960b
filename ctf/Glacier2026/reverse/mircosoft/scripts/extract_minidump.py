#!/usr/bin/env python3
"""Extract genuine memory-stream regions from GlacierCTF mircosoft MiniDump.

Usage:
  python3 scripts/extract_minidump.py mircosoft/admin_machine.dmp --out-dir work

Requires only Python stdlib. This is a parser, not a license decrypter.
"""
import argparse
import hashlib
import pathlib
import struct

EXPECTED_SHA256="a51edf96a395692dabfb67b654f4eb3f4742dd483da8d7d3d49579a369d2d5e4"

def read_regions(path):
    data=path.read_bytes()
    if len(data)<32 or data[:4]!=b"MDMP":
        raise ValueError("not a MiniDump")
    streams, directory=struct.unpack_from("<II",data,8)
    if not 0<streams<4096 or directory+streams*12>len(data):
        raise ValueError("bad stream directory")
    result=None
    for i in range(streams):
        typ,size,rva=struct.unpack_from("<III",data,directory+12*i)
        if rva+size>len(data): raise ValueError("truncated stream")
        if typ==5:
            if size<4: raise ValueError("short MemoryListStream")
            n=struct.unpack_from("<I",data,rva)[0]
            if n>256 or 4+16*n>size: raise ValueError("invalid memory regions")
            entries=[]
            for j in range(n):
                start,span,offset=struct.unpack_from("<QII",data,rva+4+16*j)
                if offset+span>len(data): raise ValueError("truncated memory region")
                entries.append((start,data[offset:offset+span]))
            result=entries
    if result is None: raise ValueError("no MemoryListStream (type 5)")
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("minidump",type=pathlib.Path)
    p.add_argument("--out-dir",type=pathlib.Path,default=pathlib.Path("."))
    args=p.parse_args()
    sha=hashlib.sha256(args.minidump.read_bytes()).hexdigest()
    print("MiniDump SHA256:",sha)
    print("Matches organizer handout:",sha==EXPECTED_SHA256)
    entries=read_regions(args.minidump)
    args.out_dir.mkdir(parents=True,exist_ok=True)
    for i,(address,data) in enumerate(entries):
        name=args.out_dir/f"region{i}.bin"
        name.write_bytes(data)
        print(f"region{i}: virtual_address=0x{address:x}, bytes={len(data)}, sha256={hashlib.sha256(data).hexdigest()}")
    if len(entries)<5:
        raise ValueError("expected at least 5 memory regions")
    samples=[("CPUID",0,0x274,48),("Volume serial",4,0x48,4),
             ("MachineGuid",1,0x500,36),("SMBIOS UUID",2,0x55,16),
             ("MAC",3,0x198,6)]
    fingerprint=b""
    for label,region,offset,size in samples:
        data=entries[region][1][offset:offset+size]
        if len(data)!=size: raise ValueError("short "+label)
        fingerprint+=data
        print(f"{label}: region{region}+0x{offset:x}, {size} bytes")
    print("Fingerprint length:",len(fingerprint))
    print("Fingerprint SHA256:",hashlib.sha256(fingerprint).hexdigest())

if __name__=="__main__":
    main()
