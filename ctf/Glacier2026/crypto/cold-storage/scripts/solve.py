#!/usr/bin/env python3
"""Portable Cold Storage solver, derived from the successful historical solver.

Explicit --url to target an existing authorized CTF instance, or --spawn to
request a new instance from the historical challenge launcher.
Python >=3.9; requires requests. No network access without these options.
"""
import argparse
import re
import socket
import time
from urllib.parse import parse_qsl

R = 0xE1000000000000000000000000000000

def mul(x, y):
    z, v = 0, x
    for i in range(128):
        if (y >> (127-i)) & 1:
            z ^= v
        v = (v >> 1) ^ (R if v & 1 else 0)
    return z

def fpow(a, power):
    result = None
    while power:
        if power & 1:
            result = a if result is None else mul(result, a)
        a = mul(a, a)
        power >>= 1
    return 0 if result is None else result

def inverse(a):
    if not a:
        raise ZeroDivisionError("GF(2^128) division by zero")
    return fpow(a, (1<<128)-2)

def sqrt(a):
    return fpow(a, 1<<127)

def integer(b):
    return int.from_bytes(b, 'big')

def ghash(h, cipher):
    assert len(cipher) == 32
    x, y = integer(cipher[:16]), integer(cipher[16:])
    return mul(mul(x, mul(h, h)) ^ mul(y, h) ^ 256, h)

def derive(first_plaintext, first_ciphertext, first_tag, second_ciphertext, second_tag):
    assert len(first_plaintext) == len(first_ciphertext) == len(second_ciphertext) == 32
    assert len(first_tag) == len(second_tag) == 16
    assert first_ciphertext[:16] == second_ciphertext[:16], "First block must match"
    difference = integer(first_ciphertext[16:]) ^ integer(second_ciphertext[16:])
    h2 = mul(integer(first_tag) ^ integer(second_tag), inverse(difference))
    h = sqrt(h2)
    mask = ghash(h, first_ciphertext) ^ integer(first_tag)
    assert (ghash(h, second_ciphertext) ^ integer(second_tag)) == mask, "GHASH verification failed"
    keystream = bytes(a^b for a,b in zip(first_plaintext, first_ciphertext))
    return h, mask, keystream

def forge(keystream, h, mask, plaintext):
    assert len(plaintext) == len(keystream) == 32
    ct = bytes(a^b for a,b in zip(plaintext, keystream))
    tag = (ghash(h, ct) ^ mask).to_bytes(16, 'big')
    return ct, tag

def request_instance():
    s = socket.create_connection(("challs.glacierctf.com", 13379), timeout=20)
    s.settimeout(20)
    s.sendall(b"\n")
    buf = b""
    while len(buf) < 8192:
        part = s.recv(4096)
        if not part:
            break
        buf += part
        match = re.search(rb"https://[0-9a-f]+\.cold-storage\.web\.glacierctf\.com", buf)
        if match:
            return match.group(0).decode(), s
    s.close()
    raise RuntimeError("Instance URL not found: " + repr(buf[:1000]))

def get_records(session, base):
    for _ in range(40):
        try:
            r = session.get(base+"/archive", timeout=8)
            if r.status_code == 200 and r.text.strip():
                values = parse_qsl(r.text)
                records, current = [], {}
                for key, value in values:
                    if key == "recorded" and current:
                        records.append(current)
                        current = {}
                    current[key] = value
                if current: records.append(current)
                return records
        except Exception:
            pass
        time.sleep(0.3)
    raise RuntimeError("Archive unavailable")

def main():
    p = argparse.ArgumentParser()
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="existing CTF instance base URL")
    group.add_argument("--spawn", action="store_true", help="open a new historical CTF instance")
    args = p.parse_args()
    import requests
    launcher = None
    try:
        if args.spawn:
            base, launcher = request_instance()
        else:
            base = args.url.rstrip("/")
        session = requests.Session()
        session.verify = True
        recs = get_records(session, base)
        record = next(x for x in recs if x["recorded"].endswith(":59Z"))
        timestamp = record["recorded"]
        alias = timestamp[:-3] + "60Z"
        sample = record["sample"][:5] + "aaa"
        if not re.fullmatch("[a-z0-9-]{2,21}", sample):
            raise RuntimeError("invalid sample alias")
        print("records", len(recs))
        print("target", timestamp, record["sample"], "alias", alias)
        response = session.post(base+"/api/import", data={"recorded":alias, "sample":sample}, timeout=15)
        print("import", response.status_code)
        response.raise_for_status()
        imp = dict(parse_qsl(response.text))
        if "ciphertext" not in imp or "tag" not in imp:
            raise RuntimeError(response.text)
        first_plain = ("open".ljust(10)+"|"+sample.ljust(21)).encode()
        c1, t1 = bytes.fromhex(imp["ciphertext"]), bytes.fromhex(imp["tag"])
        c2, t2 = bytes.fromhex(record["ciphertext"]), bytes.fromhex(record["tag"])
        h, mask, ks = derive(first_plain, c1,t1,c2,t2)
        print("GHASH key verified", h.to_bytes(16, "big").hex())
        target = ("restricted"+"|"+"Ice-VII".ljust(21)).encode()
        cf, tf = forge(ks,h,mask,target)
        result = session.post(base+"/api/open", data={
            "recorded":timestamp, "ciphertext":cf.hex(), "tag":tf.hex()}, timeout=15)
        print("forged open",result.status_code)
        fields = dict(parse_qsl(result.text))
        print("classification",fields.get("classification"),"sample",fields.get("sample"))
        print("flag returned:", bool(fields.get("flag", "").startswith("gctf{")))
        if result.status_code != 200 or "flag" not in fields:
            raise RuntimeError("Exploit unsuccessful: " + result.text[:200])
    finally:
        if launcher:
            launcher.close()

if __name__ == "__main__":
    main()
