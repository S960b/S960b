import hashlib,hmac,json,struct
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
s=open('/mnt/data/voteme_work/last_s.bin','rb').read();c=open('/mnt/data/voteme_work/last_c.bin','rb').read()
x=32112464072190682197233418229036703523437613740958407365966986320909466005514

def records(b):
 r=[];pos=0
 while pos<len(b):
  assert b[pos+1:pos+3]==b'\x03\x03'
  t=b[pos];n=int.from_bytes(b[pos+3:pos+5],'big');payload=b[pos+5:pos+5+n];assert len(payload)==n
  r.append((t,payload));pos+=5+n
 return r

cr=records(c)[0][1][6:38];sr=records(s)[0][1][6:38]
print('randoms',cr.hex(),sr.hex())

def prf(secret,label,seed,length):
 z=label+seed;a=z;out=b''
 while len(out)<length:
  a=hmac.digest(secret,a,'sha384');out+=hmac.digest(secret,a+z,'sha384')
 return out[:length]
master=prf(x.to_bytes(32,'big'),b'master secret',cr+sr,48)
block=prf(master,b'key expansion',sr+cr,72)
client_key,server_key,client_iv,server_iv=block[:32],block[32:64],block[64:68],block[68:72]
sc=records(s);cc=records(c)
print('record count',len(sc),'server last',[(t,len(b)) for t,b in sc[-3:]])

# server finished seq0; application seq1..
chunks=[];sequence=0
for t,payload in sc:
 if t not in (22,23) or len(payload)<24:continue
 if t==22 and payload[0] in (2,11,12,14):continue
 explicit=payload[:8];ciphertext_tag=payload[8:];ad=struct.pack('>Q',sequence)+bytes([t])+b'\x03\x03'+len(ciphertext_tag[:-16]).to_bytes(2,'big')
 assert int.from_bytes(explicit,'big')==sequence,(sequence,int.from_bytes(explicit,'big'))
 plaintext=AESGCM(server_key).decrypt(server_iv+explicit,ciphertext_tag,ad)
 print('Decrypted',sequence,'type',t,'len',len(plaintext),'prefix',plaintext[:40] if sequence<2 else '')
 if t==23: chunks.append(plaintext)
 sequence+=1
raw=b''.join(chunks)
with open('/mnt/data/voteme_work/votes_response.bin','wb') as f:f.write(raw)
print('plain response',len(raw),'head',repr(raw[:180]),'tail',repr(raw[-150:]))
body=raw.split(b'\r\n\r\n',1)[1]
v=json.loads(body)
print('votes',len(v['votes']))
print('first vote',json.dumps(v['votes'][0])[:1800]);print('2nd',json.dumps(v['votes'][1])[:500])
with open('/mnt/data/voteme_work/votes.json','w') as f:json.dump(v,f)
