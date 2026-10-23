#!/usr/bin/env python3
"""Offline self-test of the private-key recovery formula with two signatures."""
import base64
import hashlib
import json
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from solve_misdir import N, RP_IDS, calculate_private

secret = int('90f48953ebaa77745d15471aa717163231afcd22f987d4d6deacb3a0207e4338',16)
for rp in RP_IDS:
    cd = json.dumps({'type':'webauthn.get','challenge':'AAAB','origin':'http://test','crossOrigin':False},separators=(',',':')).encode()
    ad = hashlib.sha256(rp.encode()).digest() + bytes.fromhex('0500000001')
    z = int.from_bytes(hashlib.sha256(ad + hashlib.sha256(cd).digest()).digest(),'big')
    k = int.from_bytes(hashlib.sha256(rp.encode()).digest(),'big') % N
    r = ec.derive_private_key(k,ec.SECP256R1()).public_key().public_numbers().x % N
    s = (pow(k,-1,N)*(z+r*secret)) % N
    enc = lambda b:base64.b64encode(b).decode()
    event={'rpId':rp,'signed':{'authenticatorData':enc(ad),'clientDataJSON':enc(cd),'signature':enc(encode_dss_signature(r,s))}}
    recovered,_=calculate_private(event)
    assert recovered == secret
    print(rp,'RECOVERED',f'{recovered:064x}')
print('SELFTEST_OK')
