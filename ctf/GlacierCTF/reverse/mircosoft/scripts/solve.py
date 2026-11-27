import struct
import os
from pathlib import Path
# Keep historical default; optional work directory for standalone reproduction.
B=Path(os.environ.get('MIRCOSOFT_WORKDIR', '/mnt/data/mircosoft_work'))
exe=(B/'unpacked_pe.bin').read_bytes()
license=(B/'mircosoft/license.bin').read_bytes()
# RVA .data 0x6000 at file pos 0x3e00
D=exe[0x3e00:]
keys=[D[16*i:16*i+16] for i in range(4)]
print('keys',*[k.hex() for k in keys])
MOD=0xffffffff
U=lambda x:x&MOD

def tea(block,key):
    v0,v1=struct.unpack('<2I',block)
    k=struct.unpack('<4I',key)
    su=0
    for _ in range(32):
        v0=U(v0+((U(v1<<4)^(v1>>5))+v1 ^ (su+k[su&3])))
        su=U(su+0x9e3779b9)
        v1=U(v1+((U(v0<<4)^(v0>>5))+v0 ^ (su+k[(su>>11)&3])))
    return struct.pack('<2I',v0,v1)

def mac(state, data, k0,k1):
    vals=list(struct.unpack('<4I',state))
    for off in range(0,len(data),4):
        blk=data[off:off+4].ljust(4,b'\0')
        vals[0]^=struct.unpack('<I',blk)[0]
        vals[:2]=struct.unpack('<2I',tea(struct.pack('<2I',*vals[:2]),k0))
        vals[2:]=struct.unpack('<2I',tea(struct.pack('<2I',*vals[2:]),k1))
        vals[3]^=vals[1];vals[0]^=vals[2]
    return struct.pack('<4I',*vals)

def transform(x):
 a,b,c,d=struct.unpack('<4I',x)
 rol=lambda x,n: U((x<<n)|(x>>(32-n)))
 a=U((rol(a,7)^b)+c)
 b=U((rol(b,7)^c)+d)
 c=U((rol(c,7)^d)+a)
 d=U((rol(d,7)^a)+b)
 return struct.pack('<4I',a,b,c,d)

cpuid=(B/'region0.bin').read_bytes()[0x274:0x274+48]
vol=(B/'region4.bin').read_bytes()[0x48:0x4c]
reg=(B/'region1.bin').read_bytes()[0x500:0x500+36]
sm=(B/'region2.bin').read_bytes()
uuid=sm[0x55:0x65]
macaddr=(B/'region3.bin').read_bytes()[0x198:0x19e]
print('cpuid',repr(cpuid))
print('vol',vol.hex(),'registry',repr(reg),'uuid',uuid.hex(),'mac',macaddr.hex())
all_data=cpuid+vol+reg+uuid+macaddr
print('len',len(all_data),'input',all_data.hex())
IV=struct.pack('<4I',0x67452301,0xefcdab89,0x98badcfe,0x10325476)
print('header mac',mac(IV,all_data,keys[2],keys[3]).hex(),'vs',license[8:24].hex())
state=bytes(16)
for name,dat in [('cpuid',cpuid),('reg',vol+reg),('uuidnic',uuid+macaddr)]:
 state=mac(state,dat,keys[0],keys[1])
 print('state',name,state.hex())
key=transform(state)
print('key',key.hex())
nonce=license[24:32];ct=license[32:96]
v0,v1=struct.unpack('<2I',nonce)
plain=b''
for j in range(8):
 stream=tea(struct.pack('<2I',U(v0+j),v1),key)
 plain+=bytes(a^b for a,b in zip(ct[j*8:j*8+8],stream))
print('plaintext',plain,plain.hex())