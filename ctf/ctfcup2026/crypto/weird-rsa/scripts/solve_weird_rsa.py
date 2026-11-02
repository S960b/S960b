import re, sys

def parse(path):
    txt = open(path).read()
    p = int(re.search(r'p = (\d+)', txt).group(1))
    e = int(re.search(r'e = (\d+)', txt).group(1))
    N = [int(x) for x in re.search(r'N = \[(.*?)\]', txt, re.S).group(1).split(',')]
    C = [int(x) for x in re.search(r'c = \[(.*?)\]', txt, re.S).group(1).split(',')]
    return p, e, N, C

def add(a, b, p): 
    n = max(len(a), len(b)); return [( (a[i] if i < len(a) else 0) + (b[i] if i < len(b) else 0)) % p for i in range(n)]
def sub(a, b, p):
    n = max(len(a), len(b)); return [( (a[i] if i < len(a) else 0) - (b[i] if i < len(b) else 0)) % p for i in range(n)]
def mul(a, b, p):
    if not a or not b: return []
    out = [0]*(len(a)+len(b)-1)
    for i, x in enumerate(a):
        if x:
            for j, y in enumerate(b): out[i+j] = (out[i+j] + x*y) % p
    return out
def trim(a):
    while a and a[-1] == 0: a.pop()
    return a
def divmodp(a, b, p):
    a = trim(list(a)); b = trim(list(b))
    if not b: raise ZeroDivisionError
    inv = pow(b[-1], p-2, p)
    q = [0]*max(0, len(a)-len(b)+1)
    while len(a) >= len(b) and a:
        d = len(a)-len(b); c = a[-1]*inv % p
        q[d] = c
        for i in range(len(b)): a[i+d] = (a[i+d] - c*b[i]) % p
        trim(a)
    return trim(q), a
def modp(a, m, p):
    return divmodp(a, m, p)[1]
def mulmod(a, b, m, p):
    return modp(mul(a, b, p), m, p)
def powmod(base, exp, m, p):
    r = [1]
    b = modp(base, m, p)
    while exp:
        if exp & 1: r = mulmod(r, b, m, p)
        b = mulmod(b, b, m, p)
        exp >>= 1
    return r

p, e, N, C = parse(sys.argv[1])
phi = (p**58 - 1) * (p**59 - 1)
d = pow(e, -1, phi)
m = powmod(C, d, N, p)
# verify
assert powmod(m, e, N, p) == modp(C, N, p), "re-encryption mismatch"
s = bytes(c % 256 for c in m)
print(s.decode('ascii', 'replace'))
