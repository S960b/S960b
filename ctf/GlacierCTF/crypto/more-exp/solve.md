# More EXP: solving path

[Русская версия](solve.ru.md)

## 0. The entire plan

A 4096-bit-class RSA modulus looks intimidating. But the size of the primes is not the actual obstacle in this task. The program permits encrypting the same unknown plaintext with two public exponents of our choice. It also encrypts known integers, which leaks information about the supposedly hidden modulus.

Our solution was:

1. Read the Python service and identify the exact encryption operation and lifetime of the key.
2. Request three encryptions of known integers at exponent 65537.
3. Recover a multiple of the modulus via GCD and remove accidental small factors.
4. Encrypt the flag twice, using exponents 65537 and 65538, in the same connection.
5. Divide the two ciphertexts in modular arithmetic, convert the resulting integer back into bytes, and verify both equations.

No private exponent, factorization of the RSA modulus, or brute force over the flag is required.

## 1. Reading the source

The challenge is a Python service, supplied in the archive `more_power.tar.gz`. See [challenge](assets/more_power/challenge). In the constructor we find:

```python
p = getPrime(bit_size)
q = getPrime(bit_size)
self.N = p*q
self.phi_n = (p-1)*(q-1)
```

Although the constructor defaults to 512 bits, main explicitly creates `EncryptionService(2048)`. Thus p and q are each 2048-bit primes. Their product is either 4095 or 4096 bits: each prime is at least 2**2047 and less than 2**2048.

The encryption method is just:

```python
def encrypt(self, e: int, message: int) -> int:
    # We should pad here so we are not vulnerable to plaintext RSA attacks
    return pow(message, e, self.N)
```

Three-argument Python pow computes modular exponentiation efficiently, not the full power followed by division. Mathematically, the returned ciphertext is c = m^e mod N.

There is no randomized padding such as RSA-OAEP. Every request to encrypt the same integer under the same exponent and modulus produces the same ciphertext.

Next, the service chooses what to encrypt:

```python
if "msg" in parsed_data:
    message_to_sign = parsed_data["msg"]
else:
    message_to_sign = FLAG
```

For strings it uses bytes_to_long on the UTF-8 bytes; for integers it directly calls encrypt. The flag is read using flag_file.read(), with no strip(), so a trailing newline remains part of the plaintext.

Finally, the caller supplies e. The only constraint relevant here is:

```python
if exp < 0x10001:
    print("This exponent is too small")
    return 1
```

0x10001 equals 65537. There is no check that e is coprime to phi(N), and no restriction to a fixed public exponent. In particular, 65538 is accepted, even though it is even and cannot be coprime to the even phi(N) of an odd-prime RSA modulus.

Why this matters: we can get two algebraically related powers of exactly the same secret. The missing check on e is not alone sufficient to explain the attack; it is the combination of unrestricted exponents, raw RSA and repeated plaintext under a single modulus.

## 2. Understanding the protocol and the session

The prompt is `> `, requests are newline-delimited JSON and the ciphertext appears as hexadecimal. The welcome example shows 0x1337, but hexadecimal literals are not valid JSON numbers. We use ordinary decimal integers or json.dumps:

```json
{"e": 65537, "msg": 2}
```

Omitting msg is different from setting it to null:

```json
{"e": 65537}
```

This asks for the encrypted flag. The solver parses the hexadecimal value following `Your encrypted message for ...:`.

The service object is created outside the request loop. The Dockerfile uses socat with fork to start a separate process for each connection. Therefore all five requests must share one TCP connection. Reconnecting between modulus recovery and flag encryption would invalidate the calculation because the new process generates new primes.

## 3. Recovering the hidden modulus

The server never prints N. However, for any known integer m and ciphertext c,

```
c = m^e mod N
m^e = k*N + c
m^e - c = k*N
```

The last expression is an exact integer multiple of N. Let E = 65537, and ask the service to encrypt 2, 3 and 5:

```
d2 = 2^E - c(2)
d3 = 3^E - c(3)
d5 = 5^E - c(5)
G  = gcd(d2, d3, d5)
```

Since each difference contains N, G = N*gcd(k2,k3,k5). If the three quotients have no shared factor beyond 1, G is precisely N.

GCD (greatest common divisor) is the largest positive integer dividing its arguments. The Euclidean algorithm repeatedly replaces a pair (a,b) with (b,a mod b), until the second element is zero. Python math.gcd implements this efficiently even for large integers.

Why these particular messages? Small integers let us compute the full, non-modular powers locally. For 2^65537, the bit length is 65538, approximately 8 KiB of integer data. 3^65537 and 5^65537 are larger but still practical. The bit length of 2^65537 minus a nonzero ciphertext smaller than 2^4096 is 65537, matching the first line of the actual log.

The original code was:

```python
N = 0
samples = []
for msg in (2, 3, 5):
    c = get_c(s, E, msg)
    samples.append((msg, c))
    N = math.gcd(N, pow(msg, E) - c)
```

Starting with zero is convenient because gcd(0,x) = abs(x). Each additional sample can reduce the GCD, never increase it.

## 4. The first attempt: why the inverse failed

The first version used the GCD directly as N. The retained report reconstructs the following output from the competition conversation; the raw first-run log was overwritten:

```text
[*] known msg 2 gcd bits 65537
[*] known msg 3 gcd bits 4097
[*] known msg 5 gcd bits 4097
[+] N bits 4097
[*] ciphertexts obtained
RuntimeError: Unexpected gcd(c1,N)=2
```

A 4097-bit modulus cannot be the product of two 2048-bit primes. Something remained in the GCD beyond the true N. The error gcd(c1,G)=2 is consistent with an accidental factor 2 in G. The historical report identifies the candidate as 2*N; the individual ciphertexts and exact modulus were not retained, so we cannot independently reconstruct the exact multiplier.

This is not an RSA prime being even: p and q are large odd primes. Instead, the quotients k2,k3,k5 can happen to share a factor. Their parity is not guaranteed; reducing an even integer modulo an odd N can produce either parity. The extra factor therefore depends on the samples and the session.

Why did the known-plaintext checks not detect the bad modulus? If G is a multiple of N and divides all sampled differences, then m^E = c mod G also holds, and c is smaller than G. Those checks are necessary but are not sufficient to prove G is the true modulus.

Later we need an inverse of the first flag ciphertext. An inverse a^(-1) mod n exists if and only if gcd(a,n)=1. The erroneous even modulus and even ciphertext made that impossible.

## 5. Removing the accidental factors

The fix used in the successful run was:

```python
for prime in (2,3,5,7,11,13,17,19,23,29,31,37,41,43,47):
    while N % prime == 0 and all(pow(m,E,N//prime)==c for m,c in samples):
        N //= prime
for msg,c in samples:
    assert pow(msg,E,N)==c, 'bad modulus'
```

Why this works here: the actual prime factors are 2048-bit numbers, so none of the small primes in the list can divide the real modulus. They can only divide an accidental multiplier. The congruence checks also guard against choosing a candidate that no longer reproduces the observed residues.

In the successful retry the GCD had 4096 bits and the cleaned modulus 4095 bits. This is consistent with the source. It was a new connection, not a continuation of the first run.

A caveat: the small-prime loop is a practical fix, not a general proof of exact modulus recovery. If a larger accidental factor survives, collect additional known-plaintext samples in the same connection and recompute the GCD. A correct bit length alone does not certify the modulus. The later plaintext format and the two encryption checks supply further evidence.

## 6. Recovering the flag: common modulus attack

Now that we have N, the service will encrypt the same flag m for two exponents:

```json
{"e": 65537}
{"e": 65538}
```

Writing E = 65537, the two results are:

```
c1 = m^E     mod N
c2 = m^(E+1) mod N
```

The key observation is m^(E+1) = m^E * m, so

```
c2 = c1 * m mod N
m  = c2 * inverse(c1,N) mod N
```

Ordinary integer division c2/c1 is not valid: the ciphertexts have both been reduced modulo N. Instead, multiply by the modular inverse. Python 3.8 and later allow `pow(c1,-1,N)` to calculate it when gcd(c1,N)=1.

This is the common modulus attack with especially simple Bézout coefficients. For coprime exponents e1,e2, find integers a,b satisfying a*e1+b*e2=1. Then c1^a*c2^b gives m modulo N, when negative powers are defined. Since 65538 - 65537 = 1, our coefficients are a=-1, b=1; no general extended-GCD calculation for exponents is necessary.

The solver checks gcd(c1,N), computes the inverse, and converts the result:

```python
g = math.gcd(c1,N)
if g != 1:
    raise RuntimeError('Unexpected gcd(c1,N)='+str(g))
m = (c2*pow(c1,-1,N)) % N
raw = m.to_bytes((m.bit_length()+7)//8,'big')
```

The recovered flag is 70 printable bytes (gctf{, 64 hex characters, }), plus one newline, so it fits comfortably below N. Big-endian conversion is the inverse of bytes_to_long for this nonzero-leading plaintext. If a message were at least N, the modular calculation would recover only its residue, not the full original bytes.

Finally, re-encrypt the result:

```python
assert pow(m,E,N)==c1
assert pow(m,E+1,N)==c2
```

Both checks passed. These are stronger than merely seeing a flag-shaped string. The output was a correctly formatted flag with the newline expected from the source.

## 7. Reproduce locally

The original unpacked files are in assets/more_power. The organizer's flag.txt is a dummy, not the real flag. The competition solver is preserved except that the host and port now point at localhost rather than the expired instance. It needs only Python standard library modules (socket, json, re, math).

Start the service in one terminal, with Python 3 and socat installed:

```sh
cd assets/more_power
sha256sum -c sha256sum
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
socat TCP-LISTEN:1337,bind=127.0.0.1,reuseaddr,fork EXEC:"python3 -u challenge"
```

In another terminal, from the challenge's write-up directory:

```sh
python3 -u scripts/glacier_more_exp.py
```

Alternatively, the organizer provides deploy.sh with check/build/run subcommands for Docker. Do not replace flag.txt with the competition flag. Stop the service after testing.

During preparation of this write-up, we repeated the five-request attack against the exact supplied challenge code, on loopback, using a temporary compatibility module for the two Crypto.Util.number functions because PyCryptodome was not installed in the analysis environment. The replacement called OpenSSL to generate 2048-bit primes and int.from_bytes for conversion; it did not change the service's RSA or request logic. The original solver was changed only at the destination. Both checks passed with a newly generated 4096-bit modulus. See [local-redacted.log](evidence/local-redacted.log). This is not a full Docker/PyCryptodome integration test.

## 8. Competition result and limitations

The successful preserved run:

```text
[*] known msg 2 gcd bits 65537
[*] known msg 3 gcd bits 4098
[*] known msg 5 gcd bits 4096
[*] gcd bits 4096
[+] N bits 4095
[*] ciphertexts obtained
[+] RAW b'gctf{...}\n'
[+] FLAG gctf{...}

[+] BOTH RSA CHECKS PASSED
```

The remote flag was recovered during the competition. The original success log exists and is published with the flag masked. No platform submission receipt was supplied; the first-run log and exact numeric RSA parameters are not retained. The live competition endpoint was not contacted while preparing this write-up.

## 9. What should be fixed

A large RSA modulus does not protect a poorly designed encryption interface. The service should not expose raw RSA encryption of a secret at arbitrary client-selected exponents. Use a vetted cryptographic library, fixed properly generated keys, RSA-OAEP for encryption (or an appropriate hybrid authenticated-encryption scheme), and RSA-PSS for signatures. Do not expose the secret as an implicit plaintext when a request field is omitted. Hiding the public modulus is not a security boundary.
