#!/usr/bin/env python3
"""Build a spectrogram of a fragment of ventilator303.wav.

Used to see the two AFSK tones (1200 and 2400 Hz) instead of guessing from the
waveform. Needs Python 3, numpy and matplotlib.

Usage: python3 03_spectrum.py ventilator303.wav --start 15 --length 0.25 --out fsk_detail.png
"""
import argparse
import wave

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        fs = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64)
    return fs, x


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wav")
    ap.add_argument("--start", type=float, default=15.0, help="seconds to skip")
    ap.add_argument("--length", type=float, default=0.25, help="fragment length in seconds")
    ap.add_argument("--frame", type=int, default=1024, help="FFT window in samples")
    ap.add_argument("--hop", type=int, default=128, help="window step in samples")
    ap.add_argument("--out", default="fsk_detail.png")
    args = ap.parse_args()

    fs, x = read_wav(args.wav)
    a = int(args.start * fs)
    b = a + int(args.length * fs)
    seg = x[a:b]
    if len(seg) < args.frame:
        raise SystemExit("fragment shorter than one FFT window")

    win = np.hanning(args.frame)
    frames = 1 + (len(seg) - args.frame) // args.hop
    spec = np.empty((args.frame // 2 + 1, frames))
    for i in range(frames):
        chunk = seg[i * args.hop:i * args.hop + args.frame] * win
        spec[:, i] = np.abs(np.fft.rfft(chunk))
    db = 20 * np.log10(spec + 1e-9)
    freqs = np.fft.rfftfreq(args.frame, 1 / fs)

    band = freqs <= 5000
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.imshow(db[band], aspect="auto", origin="lower", cmap="magma",
              extent=(args.start, args.start + len(seg) / fs, freqs[band][0], freqs[band][-1]))
    ax.set_xlabel("time, s")
    ax.set_ylabel("frequency, Hz")
    ax.set_title("ventilator303.wav, fragment %.2f s, %.0f Hz sample rate" % (args.start, fs))
    fig.tight_layout()
    fig.savefig(args.out, dpi=110)
    print("saved", args.out, "frames", frames, "window", args.frame)

    totals = spec.sum(axis=1)
    top = np.argsort(totals)[-6:][::-1]
    print("strongest frequencies:", sorted(round(float(freqs[i])) for i in top))


if __name__ == "__main__":
    main()
