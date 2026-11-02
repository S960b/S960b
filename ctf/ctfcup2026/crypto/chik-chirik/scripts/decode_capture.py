#!/usr/bin/env python3
"""CTFCUP transmitter waveform demodulator, extracts chirp/phase octal symbols.
Usage: python3 decode_capture.py capture.wav
Needs numpy, scipy. Does not invert the source's randomization or CRC.
"""
import sys
import numpy as np
from scipy.io import wavfile

FS = 48000
N = 2048
DELTA = np.array([-2000, -1000, 1000, 2000])
T = np.arange(N, dtype=float)
CHIRPS = np.exp(-2j * np.pi * (6000 * T[None, :] / FS + DELTA[:, None] * T[None, :]**2 / (2 * N * FS)))

def demodulate(samples):
    count = len(samples) // N
    arr = np.asarray(samples[:count * N], dtype=np.float64).reshape(count, N)
    result = np.empty(count, dtype=np.uint8)
    confidence = np.empty(count, dtype=float)
    for start in range(0, count, 256):
        end = min(start + 256, count)
        z = arr[start:end] @ CHIRPS.T
        mag = np.abs(z)
        tone = np.argmax(mag, axis=1)
        selected = z[np.arange(end - start), tone]
        result[start:end] = tone + 4 * (np.real(selected) < 0)
        confidence[start:end] = mag.max(axis=1) / np.maximum(1.0, mag.sum(axis=1))
    return result, confidence

def main(filename):
    sample_rate, wav = wavfile.read(filename)
    if sample_rate != FS:
        raise ValueError(f'Expected {FS} Hz, got {sample_rate}')
    if wav.ndim != 1:
        raise ValueError('Expected mono WAV')
    symbols, cf = demodulate(wav)
    print('sample_rate:', sample_rate)
    print('samples:', len(wav))
    print('chirps:', len(symbols))
    print('tone_confidence_median:', float(np.median(cf)))
    print('preamble_octals:', ''.join(map(str,symbols[:8])))
    blocks = symbols[8:]
    print('blocks_len:', len(blocks) // 7)
    if len(blocks) % 7: print('WARNING: incomplete last block')
    if len(blocks) % 7 == 0:
        b = blocks.reshape(-1,7)
        bad = 0
        for mask in ((0,2,4,6),(1,2,5,6),(3,4,5,6)):
            syndrome = np.bitwise_xor.reduce(b[:,mask],axis=1)
            bad += int((syndrome!=0).sum())
        print('Hamming_GF8_nonzero_syndromes:', bad)
    s = ''.join(map(str,symbols))
    out = filename + '.octal.txt'
    with open(out,'w') as f:f.write(s+'\n')
    print('octal_symbol_string:',s)
    print('saved:',out)

if __name__=='__main__':
    if len(sys.argv)!=2:
        raise SystemExit('Usage: python3 decode_capture.py capture.wav')
    main(sys.argv[1])
