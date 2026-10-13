#!/usr/bin/env python3
"""Offline, challenge-specific BIP39, BIP32, Ethereum, AES-GCM verifier.
No real wallet access, no network, no brute-forcing random seed phrases.
"""
import argparse, base64, hashlib, hmac, struct, unicodedata, zipfile
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TARGET = 'dc7d8b278af5f31fed5fe68f50be6d02404fb522'
NONCE = 'NaY6LHeKYbf9abvi'
CIPHERTEXT = '7ue/WCpa19Wp+mKFu1PHsuTknMRzHqRamlwGVXWJCmMw7smZ/9PzoWkhi80Ezgs9tQ=='
LABEL = b'FortID/cryptocurrency-wallet/flag/v1'
AAD = b'wallet-recovery'
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
RC=[0x0000000000000001,0x0000000000008082,0x800000000000808A,0x8000000080008000,0x000000000000808B,0x0000000080000001,0x8000000080008081,0x8000000000008009,0x000000000000008A,0x0000000000000088,0x0000000080008009,0x000000008000000A,0x000000008000808B,0x800000000000008B,0x8000000000008089,0x8000000000008003,0x8000000000008002,0x8000000000000080,0x000000000000800A,0x800000008000000A,0x8000000080008081,0x8000000000008080,0x0000000080000001,0x8000000080008008]
R=[[0,36,3,41,18],[1,44,10,45,2],[62,6,43,15,61],[28,55,25,21,56],[27,20,39,8,14]]
MASK=(1<<64)-1

def rol(x,n):return ((x<<n)|(x>>(64-n)))&MASK if n else x

def keccak256(data):
  size=136
  pad=bytearray(data)+b'\x01'
  pad+=b'\0'*((-len(pad)-1)%size)
  pad+=b'\x80'
  st=[0]*25
  for off in range(0,len(pad),size):
    b=pad[off:off+size]
    for i in range(size//8):st[i]^=int.from_bytes(b[8*i:8*i+8],'little')
    for rc in RC:
      c=[st[x]^st[x+5]^st[x+10]^st[x+15]^st[x+20] for x in range(5)]
      for x in range(5):
        d=c[(x-1)%5]^rol(c[(x+1)%5],1)
        for y in range(5):st[x+5*y]^=d
      b2=[0]*25
      for x in range(5):
        for y in range(5):b2[y+5*((2*x+3*y)%5)]=rol(st[x+5*y],R[x][y])
      for x in range(5):
        for y in range(5):st[x+5*y]=b2[x+5*y]^((~b2[(x+1)%5+5*y])&b2[(x+2)%5+5*y])
      st[0]^=rc
  return b''.join(t.to_bytes(8,'little') for t in st)[:32]

def bip39_valid(words, dictionary):
  if len(words)!=12 or any(w not in dictionary for w in words):return False
  indices=[dictionary.index(w) for w in words]
  bits=''.join(f'{i:011b}' for i in indices)
  entropy=int(bits[:128],2).to_bytes(16,'big')
  return int(bits[128:],2)==(hashlib.sha256(entropy).digest()[0]>>4)

def pubkey(priv):
  numbers=ec.derive_private_key(int.from_bytes(priv,'big'),ec.SECP256K1()).public_key().public_numbers()
  return numbers.x.to_bytes(32,'big'),numbers.y.to_bytes(32,'big')

def derive(words):
  phrase=unicodedata.normalize('NFKD',' '.join(words)).encode()
  salt=unicodedata.normalize('NFKD','mnemonic').encode()
  seed=hashlib.pbkdf2_hmac('sha512',phrase,salt,2048,64)
  result=hmac.new(b'Bitcoin seed',seed,hashlib.sha512).digest()
  priv=int.from_bytes(result[:32],'big');chain=result[32:]
  if not 0<priv<N:raise ValueError('invalid master private key')
  for index in (0x8000002c,0x8000003c,0x80000000,0,0):
    if index&0x80000000:data=b'\0'+priv.to_bytes(32,'big')
    else:
      x,y=pubkey(priv.to_bytes(32,'big'))
      data=bytes([2|(y[-1]&1)])+x
    data+=struct.pack('>I',index)
    result=hmac.new(chain,data,hashlib.sha512).digest()
    added=int.from_bytes(result[:32],'big')
    if added>=N:raise ValueError('invalid BIP32 child')
    priv=(priv+added)%N
    if priv==0:raise ValueError('invalid BIP32 child')
    chain=result[32:]
  key=priv.to_bytes(32,'big')
  x,y=pubkey(key)
  addr=keccak256(x+y)[-20:].hex()
  return addr,key

def main():
  ap=argparse.ArgumentParser()
  ap.add_argument('--apk',required=True)
  ap.add_argument('--mnemonic',help='12 candidate words, enclosed in quotes')
  ap.add_argument('--candidates',help='file of one 12-word candidate per line')
  a=ap.parse_args()
  with zipfile.ZipFile(a.apk) as z:
    wl=z.read('assets/bip39_english.txt').decode().splitlines()
  phrases=[a.mnemonic] if a.mnemonic else []
  if a.candidates:phrases+=open(a.candidates).read().splitlines()
  if not phrases:ap.error('provide --mnemonic or --candidates')
  for phrase in phrases:
    words=phrase.strip().lower().split()
    if not bip39_valid(words,wl):print('invalid BIP39:',phrase);continue
    addr,key=derive(words)
    print('address=0x'+addr,'match=',addr==TARGET)
    if addr==TARGET:
      aeskey=hashlib.sha256(key+LABEL).digest()
      try:
        result=AESGCM(aeskey).decrypt(base64.b64decode(NONCE),base64.b64decode(CIPHERTEXT),AAD)
        print('CHALLENGE FLAG:',result.decode('utf-8','replace'))
      except Exception as e:print('AES-GCM decrypt rejected',type(e).__name__)

if __name__=='__main__':main()
