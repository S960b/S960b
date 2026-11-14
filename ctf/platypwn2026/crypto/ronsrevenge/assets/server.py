import functools
import itertools
import math
import os
import random
import sys

class RC4_1:
    MIN_STATE_SIZE = 8

    def __init__(self, key, state_size):
        self.key = key
        self.key_length = len(self.key)
        assert state_size >= self.MIN_STATE_SIZE
        self.state_size = state_size
        self.output_bits = int(math.log2(self.state_size))
        assert 2**self.output_bits == self.state_size
        self.a = 0
        self.b = 0
        self.ksa()

    def ksa(self):
        self.state = [i for i in range(self.state_size)]
        j = 0
        for i in range(self.state_size):
            j = (j + self.state[i] + self.key[i % self.key_length]) % self.state_size
            self.state[i], self.state[j] = self.state[j], self.state[i]

    def __iter__(self):
        return self

    def __next__(self):
        self.a = (self.a + 1) % self.state_size
        self.b = (self.b + self.state[self.a]) % self.state_size
        self.state[self.a], self.state[self.b] = self.state[self.b], self.state[self.a]
        return self.state[(self.state[self.a] + self.state[self.b]) % self.state_size]


class Stream:
    def __init__(self, key, prg_state_size):
        self.prg = RC4_1(key, prg_state_size)
        self.current_bits = 0
        self.current_state = 0

    def get_prg_byte(self):
        while self.current_bits < 8:
            output = self.prg.__next__()
            self.current_state = (self.current_state << self.prg.output_bits) + output
            self.current_bits += self.prg.output_bits
        leftover_bits = self.current_bits - 8
        prg_byte = self.current_state >> leftover_bits
        self.current_state &= 2**leftover_bits - 1
        self.current_bits -= 8
        return prg_byte

    def encrypt(self, plaintext):
        return b''.join([bytes([b ^ self.get_prg_byte()]) for b in plaintext])


def test():
    prg = RC4_1(b'Key', 256)
    stream = b''.join([bytes([a]) for a in itertools.islice(prg, 10)])
    assert stream == bytes.fromhex('EB9F7781B734CA72A719')
    cipher = Stream(b'Key', 256)
    assert cipher.encrypt(b'Plaintext') == bytes.fromhex('BBF316E8D940AF0AD3')

    prg = RC4_1(b'Wiki', 256)
    stream = b''.join([bytes([a]) for a in itertools.islice(prg, 6)])
    assert stream == bytes.fromhex('6044DB6D41B7')
    cipher = Stream(b'Wiki', 256)
    assert cipher.encrypt(b'pedia') == bytes.fromhex('1021BF0420')

    prg = RC4_1(b'Secret', 256)
    stream = b''.join([bytes([a]) for a in itertools.islice(prg, 8)])
    assert stream == bytes.fromhex('04D46B053CA87B59')
    cipher = Stream(b'Secret', 256)
    assert cipher.encrypt(b'Attack at dawn') == bytes.fromhex('45A01F645FC35B383552544B9BF5')


def encryption_service(flag, state_size):
    random.seed(flag)
    longterm_key = [random.randrange(state_size) for _ in range(8)]
    print(longterm_key, file=sys.stderr)

    while True:
        try:
            iv = list(map(lambda s: int(s, 16), input('IV: ').strip().split(',')))
            assert len(iv) == 3
            assert all(map(lambda i: i >= 0 and i < state_size, iv))
        except (ValueError, AssertionError):
            print('Invalid input!')
            continue
        cipher = Stream(iv + longterm_key, state_size)
        ciphertext = cipher.encrypt(flag)
        print(ciphertext.hex())


def main():
    test()
    flag = os.getenv('FLAG')
    if flag is None:
        print('Please set the flag via env variable!')
        sys.exit(1)

    print('I will give you the encrypted flag as often as you like!')
    encryption_service(flag.encode(), 2048)


if __name__ == '__main__':
    main()
