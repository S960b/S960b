#!/usr/bin/env python3
"""Recover the NeuroPet flag from the embedded NRBC bytecode in the APK.

This script uses only Python's standard library and does not launch the app,
load Android shared libraries, patch APK files, or need an emulator.
Usage: python3 scripts/02_solve_neurovm.py neuro_extracted/base.apk --out results
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import struct
import zipfile

MASK = 0xffffffff


def le_u16(buf: bytes, offset: int) -> int:
    return struct.unpack_from('<H', buf, offset)[0]


def le_u32(buf: bytes, offset: int) -> int:
    return struct.unpack_from('<I', buf, offset)[0]


def unpack_brain(data: bytes):
    if data[:4] != b'NRBC':
        raise ValueError('Bad brain.bin magic (expected NRBC)')
    version, count = struct.unpack_from('<HH', data, 4)
    if version != 1 or count > 20:
        raise ValueError(f'Unsupported NRBC v{version} or count={count}')
    p, result = 8, {}
    for i in range(count):
        n = data[p]
        p += 1
        name = data[p:p+n].decode('ascii')
        p += n
        seed, length = struct.unpack_from('<II', data, p)
        p += 8
        encrypted = data[p:p+length]
        if len(encrypted) != length:
            raise ValueError('Truncated NRBC script')
        p += length
        state, output = seed, bytearray()
        for x in encrypted:
            state = (state * 0x41c64e6d + 0x3039) & MASK
            output.append(x ^ ((state >> 16) & 255))
        result[name] = {'seed': seed, 'length': length, 'program': bytes(output)}
        print(f'NRBC[{i}]: name={name} seed=0x{seed:08x} length={length}')
    if p != len(data):
        raise ValueError('Extra bytes after NRBC records')
    return result


def run_neuro_vm(b: bytes, seed: int, max_steps: int = 200000):
    """Minimal stack VM model for the opcodes reachable in the devroom script.

    Opcode bytes and operations come from the native x86_64 libneurocore.so
    dispatch table. All results are checked against the challenge's four
    NEUROC fragments; this does not emulate Android/JNI or unrelated opcodes.
    """
    stack = [0]
    pc = 0
    output = {}
    counter = Counter()

    def pop():
        if not stack:
            raise ValueError(f'VM stack underflow at PC 0x{pc:x}')
        return stack.pop()

    def push(v):
        if len(stack) >= 4096:
            raise ValueError('VM stack overflow')
        stack.append(v & MASK)

    def both(f):
        right = pop()
        left = pop()
        push(f(left, right))

    for _ in range(max_steps):
        if not 0 <= pc < len(b):
            raise ValueError(f'VM PC 0x{pc:x} out of range')
        op, old_pc = b[pc], pc
        pc += 1
        counter[f'0x{op:02x}'] += 1

        if op == 0x14:  # push immediate uint32
            push(le_u32(b, pc))
            pc += 4
        elif op == 0x6f:  # jump to absolute bytecode offset
            pc = le_u32(b, pc)
        elif op == 0x29:
            both(lambda a, c: a + c)
        elif op == 0x30:
            both(lambda a, c: c - a)
        elif op == 0x3e:
            both(lambda a, c: a ^ c)
        elif op == 0x37:
            both(lambda a, c: a * c)
        elif op == 0x22:
            push(stack[-1])
        elif op == 0x1b:
            pop()
        elif op == 0x5a:  # LCG iteration on top-of-stack
            stack[-1] = (stack[-1] * 0x41c64e6d + 0x3039) & MASK
        elif op == 0x53:  # byte rotate left by immediate count
            n = b[pc] & 7
            pc += 1
            v = pop() & 255
            push(((v << n) | (v >> (8-n))) & 255)
        elif op == 0x45:
            both(lambda a, c: c << (a & 31))
        elif op == 0x4c:
            both(lambda a, c: c >> (a & 31))
        elif op == 0x61:
            both(lambda a, c: int(a == c))
        elif op == 0x68:
            destination = le_u32(b, pc)
            pc += 4
            if not pop():
                pc = destination
        elif op == 0x7d:  # fragment emitter: [op,type,uint16 length]
            typ = b[pc]
            length = le_u16(b, pc+1)
            pc += 3
            if length > 128 or length > len(stack):
                raise ValueError('Invalid fragment size')
            raw = bytes(pop() & 255 for _ in range(length))[::-1]
            if typ == 1:
                encoded = b'NEUROC1!' + raw
            elif typ == 2:
                key = (seed >> 8) & 0xff  # seed=0xd2020289 -> key 0x02
                encoded = b'NEUROC2!' + bytes(x ^ key for x in raw)
            elif typ == 3:
                encoded = b'NEUROC3!' + raw[::-1]
            elif typ == 4:
                encoded = b'NEUROC4!' + raw.decode('ascii').encode('utf-16le')
            else:
                raise ValueError('Unsupported fragment type')
            output[typ] = encoded
            print(f'Emit type={typ} length={length:2d} bytecode_pc=0x{old_pc:04x}: {encoded!r}')
        elif op == 0x0d:  # script termination after the four emissions
            if len(output) < 4:
                raise ValueError('Unexpected terminator before all fragments')
            break
        elif op == 0x84:
            pass
        else:
            raise ValueError(f'Unknown opcode 0x{op:02x} at PC 0x{old_pc:x}')
    else:
        raise ValueError('VM exceeded instruction budget')
    if set(output) != {1, 2, 3, 4}:
        raise ValueError('Not all 4 fragments were emitted')
    print('VM instruction count:', sum(counter.values()))
    return output


def decode_fragment(index, data, key):
    prefix = f'NEUROC{index}!'.encode('ascii')
    if not data.startswith(prefix):
        raise ValueError(f'Bad stage {index} prefix')
    p = data[len(prefix):]
    if index == 1:
        return p.decode('ascii')
    if index == 2:
        return bytes(x ^ key for x in p).decode('ascii')
    if index == 3:
        return p[::-1].decode('ascii')
    if index == 4:
        return p.decode('utf-16le')
    raise ValueError('Unexpected fragment index')


def main():
    a = argparse.ArgumentParser()
    a.add_argument('apk', help='base.apk extracted from Android Backup')
    a.add_argument('--out', default='neuro_vm_output')
    opts = a.parse_args()
    root = Path(opts.out)
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(opts.apk) as z:
        brain = z.read('assets/neuro/brain.bin')
        info = {p: z.getinfo(p).file_size for p in
                ('assets/neuro/skin.dat','assets/neuro/weights.bin',
                 'lib/arm64-v8a/libneurocore.so','lib/x86_64/libneurocore.so')}
    print('APK auxiliary files:', json.dumps(info))
    (root / 'brain.bin').write_bytes(brain)
    records = unpack_brain(brain)
    for name, record in records.items():
        (root / f'{name}_decrypted.vm.bin').write_bytes(record['program'])
    room = records.get('devroom')
    if room is None:
        raise ValueError('devroom not found')
    output = run_neuro_vm(room['program'], room['seed'])
    for number, payload in output.items():
        (root / f'fragment{number}_encoded.bin').write_bytes(payload)
    key = (room['seed'] >> 8) & 0xff
    fragments = [decode_fragment(i, output[i], key) for i in range(1, 5)]
    for i, frag in enumerate(fragments, 1):
        print(f'Decoded fragment {i}: {frag}')
    flag = ''.join(fragments)
    print('\nFLAG:', flag)
    (root / 'flag.txt').write_text(flag + '\n', encoding='utf-8')
    if not flag.startswith('SOLARCTF{') or not flag.endswith('}'):
        raise ValueError('Unexpected output format: check emulator')
    print('Saved:', root / 'flag.txt')


if __name__ == '__main__':
    main()
