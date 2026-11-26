#!/usr/bin/env python3
"""Factor the weak 1024-bit RSA modulus from GlacierCTF LosVault.

Supported input: X.509 certificate (PEM/DER), RSA public key
(PEM/DER), or decimal/0x hexadecimal modulus.

Examples:
    python3 losvault_fermat.py internal_tls_certificate.pem
    python3 losvault_fermat.py internal_pubkey.pem --out vault_internal.key
    python3 losvault_fermat.py --modulus 0xABCDEF...

Requires: pip install cryptography
"""
import argparse
from math import isqrt, lcm
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def load_numbers(path):
    data = Path(path).read_bytes()
    for loader in (
        x509.load_pem_x509_certificate,
        x509.load_der_x509_certificate,
        serialization.load_pem_public_key,
        serialization.load_der_public_key,
    ):
        try:
            obj = loader(data)
            pub = obj.public_key() if isinstance(obj, x509.Certificate) else obj
            if isinstance(pub, rsa.RSAPublicKey):
                return pub.public_numbers().n, pub.public_numbers().e
        except (ValueError, TypeError, NotImplementedError):
            continue
    raise ValueError('Could not read an RSA public key or X.509 certificate')


def factor(n, max_steps):
    a = isqrt(n)
    if a * a < n:
        a += 1
    for i in range(max_steps):
        b_squared = a * a - n
        b = isqrt(b_squared)
        if b * b == b_squared:
            p, q = a - b, a + b
            if p > 1 and p * q == n:
                return p, q, i + 1
        a += 1
    raise ValueError(f'No close prime factors found within {max_steps} Fermat iterations')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('public_key', nargs='?', help='PEM/DER certificate or public key of the INTERNAL service')
    ap.add_argument('--modulus', help='Public RSA modulus, decimal or 0x-prefixed hex')
    ap.add_argument('--exponent', type=int, default=65537)
    ap.add_argument('--out', default='vault_internal.key', help='Output private key path')
    ap.add_argument('--max-steps', type=int, default=32)
    args = ap.parse_args()
    if bool(args.public_key) == bool(args.modulus):
        ap.error('Specify exactly one input: public_key or --modulus')
    if args.modulus:
        n, e = int(args.modulus, 0), args.exponent
    else:
        n, e = load_numbers(args.public_key)
    print(f'RSA modulus bit length: {n.bit_length()}, exponent: {e}')
    if n.bit_length() != 1024:
        raise SystemExit('Not the expected vulnerable INTERNAL 1024-bit key; do not factor the public 2048-bit HTTPS reverse-proxy certificate.')
    p, q, iters = factor(n, args.max_steps)
    d = pow(e, -1, lcm(p - 1, q - 1))
    private = rsa.RSAPrivateNumbers(
        p=p, q=q, d=d, dmp1=d % (p - 1), dmq1=d % (q - 1),
        iqmp=pow(q, -1, p), public_numbers=rsa.RSAPublicNumbers(e=e, n=n),
    ).private_key()
    Path(args.out).write_bytes(private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    print(f'Factors found in {iters} Fermat iteration(s)')
    print(f'Saved private key: {args.out}')


if __name__ == '__main__':
    main()