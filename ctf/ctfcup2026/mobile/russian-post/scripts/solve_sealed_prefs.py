#!/usr/bin/env python3
"""CTFCUP 2026 sealed-prefs APK solver. Uses only the backend embedded in the APK.
Standard-library only; tested against the static Java + Go native call flow.
"""
import argparse, base64, json, re, sys, urllib.request, urllib.error

BASE = ''  # set with --base, the instance address is not published here
UA = 'sealed-prefs/2 (Android CTF challenge client)'

def api(path, data=None, base=BASE):
    headers = {'User-Agent': UA, 'Accept': 'application/json'}
    body = None
    if data is not None:
        body = json.dumps(data, separators=(',', ':')).encode()
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(base + path, data=body, headers=headers, method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = r.read(100_000).decode('utf-8', 'replace')
            print(f'[{path}] HTTP {r.status}: {raw}')
    except urllib.error.HTTPError as e:
        raw=e.read(100_000).decode('utf-8', 'replace')
        raise RuntimeError(f'{path} HTTP {e.code}: {raw}') from e
    return json.loads(raw)

def getfield(obj, names):
    for name in names:
        if isinstance(obj, dict) and obj.get(name):
            return obj[name]
    if isinstance(obj, dict):
        for val in obj.values():
            if isinstance(val, dict):
                f=getfield(val,names)
                if f is not None:return f
    return None

def b64(raw):
    if isinstance(raw, list):return bytes(raw)
    if not isinstance(raw,str):raise ValueError(f'expected base64 string, got {type(raw).__name__}')
    text=raw.strip()
    if text.startswith('0x'):return bytes.fromhex(text[2:])
    return base64.urlsafe_b64decode(text + '=' * (-len(text)%4))

def decrypt(ciphertext, iv, key):
    """Exact Go native routine: P[i] = C[i] XOR preceding_cipher_block[i] XOR key[i]."""
    if len(iv)!=16 or len(key)!=16 or not ciphertext or len(ciphertext)%16:
        raise ValueError(f'bad sizes: sealed={len(ciphertext)}, iv={len(iv)}, key={len(key)}')
    result=bytearray()
    prev=iv
    for i in range(0,len(ciphertext),16):
        block=ciphertext[i:i+16]
        result.extend(block[j] ^ prev[j] ^ key[j] for j in range(16))
        prev=block
    pad=result[-1]
    if not 1<=pad<=16 or bytes(result[-pad:])!=bytes([pad])*pad:
        raise ValueError('PKCS#7 padding failed: bad key or mismatched parcel')
    return bytes(result[:-pad])

def main():
    p=argparse.ArgumentParser(description='CTFCUP sealed-prefs APK, backend-aware solver')
    p.add_argument('--base',required=True,help='challenge backend, for example https://<host>')
    p.add_argument('--fingerprint',default='google/panther/panther:14/UP1A.231005.007/10754064:user/release-keys')
    p.add_argument('--model',default='Pixel 7')
    p.add_argument('--android-id',default='d3c0ffeebadc0ded')
    p.add_argument('--rooted',action='store_true')
    a=p.parse_args()
    try:
        cfg=api('/api/config',base=a.base)
        body={
            'package':'com.ctf.sealedprefs', 'version':2,
            'challenge':cfg['challenge'], 'expires_at':cfg['expires_at'],
            'session':cfg['session'], 'rooted':a.rooted,
            'fingerprint':a.fingerprint,'model':a.model,'android_id':a.android_id
        }
        parcel=api('/api/parcel',body,base=a.base)
        body['receipt']=parcel['receipt']
        keys=api('/api/key',body,base=a.base)
        key_text=getfield(keys,('key','key_b64','parcel_key','parcelKey','secret_key','decryption_key'))
        if key_text is None:
            raise ValueError('Key absent in response; see full JSON above and inspect backend response schema')
        key=b64(key_text)
        # the deployed server names the container field 'parcel', the app reads 'sealed_parcel'
        sealed=parcel.get('parcel') or parcel.get('sealed_parcel')
        if not sealed: raise ValueError('no sealed container in /api/parcel response: '+json.dumps(parcel)[:300])
        ct=b64(sealed)
        iv=b64(parcel['iv'])
        pt=decrypt(ct,iv,key)
        print('\n[+] Decrypted plaintext:',repr(pt))
        flags=re.findall(rb'(?:ctfcup|CTFCUP|FortID|flag)\{[^}]+\}',pt)
        for flag in flags: print('\n[FLAG]',flag.decode(errors='replace'))
        if not flags:
            print('[!] No standard flag pattern. Inspect decrypted plaintext above.')
    except Exception as e:
        print('[!] Error:',e,file=sys.stderr)
        sys.exit(1)

if __name__=='__main__':main()
