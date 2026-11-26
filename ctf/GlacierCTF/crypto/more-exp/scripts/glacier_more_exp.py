#!/usr/bin/env python3
import socket, json, re, math

HOST='127.0.0.1'
PORT=1337
E=65537

def recv_prompt(s):
    data=bytearray()
    while not data.endswith(b'> '):
        chunk=s.recv(8192)
        if not chunk:
            raise ConnectionError('Disconnected: '+repr(bytes(data[-500:])))
        data.extend(chunk)
    return bytes(data)

def get_c(s, e, msg=None):
    request={'e': e}
    if msg is not None:
        request['msg']=msg
    s.sendall(json.dumps(request).encode()+b'\n')
    response=recv_prompt(s)
    match=re.search(rb'Your encrypted message for\s+\d+:\s*(0x[0-9a-fA-F]+)',response)
    if not match:
        raise RuntimeError('Unexpected output: '+repr(response))
    return int(match.group(1),16)

with socket.create_connection((HOST,PORT),timeout=30) as s:
    s.settimeout(30)
    print(recv_prompt(s).decode(errors='replace'), flush=True)
    N=0
    samples=[]
    for msg in (2,3,5):
        c=get_c(s,E,msg)
        samples.append((msg,c))
        N=math.gcd(N,pow(msg,E)-c)
        print('[*] known msg',msg,'gcd bits',N.bit_length(),flush=True)
    print('[*] gcd bits',N.bit_length(),flush=True)
    for prime in (2,3,5,7,11,13,17,19,23,29,31,37,41,43,47):
        while N % prime == 0 and all(pow(m,E,N//prime)==c for m,c in samples):
            N //= prime
    print('[+] N bits',N.bit_length(),flush=True)
    for msg,c in samples:
        assert pow(msg,E,N)==c, 'bad modulus'
    c1=get_c(s,E)          # omitted msg = FLAG
    c2=get_c(s,E+1)        # same FLAG, new exponent
    print('[*] ciphertexts obtained',flush=True)
    g=math.gcd(c1,N)
    if g!=1:
        raise RuntimeError('Unexpected gcd(c1,N)='+str(g))
    m=(c2*pow(c1,-1,N))%N
    raw=m.to_bytes((m.bit_length()+7)//8,'big')
    print('[+] RAW',repr(raw),flush=True)
    print('[+] FLAG',raw.decode(errors='replace'),flush=True)
    assert pow(m,E,N)==c1, 'c1 re-encrypt failed'
    assert pow(m,E+1,N)==c2, 'c2 re-encrypt failed'
    print('[+] BOTH RSA CHECKS PASSED',flush=True)
