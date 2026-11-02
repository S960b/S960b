#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_model.py -- build the weights for the "guard" transformer used by filter.

The guard is a tiny byte-level transformer that reads the submitted shellcode as
a sequence of byte tokens and outputs a single "malicious" score. The service
executes the shellcode only when the score stays at/below zero (benign).

Architecture (one block, one head):

    tok[i]      in 0..255
    x[i]        = E[tok[i]]                         # (D,) embedding
    s[i][j]     = (Wq x[i]) . (Wk x[j]) * scale
                  + relbias[i-j]                    # causal window, j in [i-W, i]
    a[i]        = softmax_j s[i][j]
    ctx[i]      = sum_j a[i][j] * (Wv x[j])         # (D,)
    z[i]        = concat(x[i], ctx[i])              # (2D,)
    h[i]        = relu(W1 z[i] + b1)                # (H,)
    logit[i]    = w2 . h[i] + b2                    # scalar
    score       = max_i logit[i]
    malicious   = score > 0

The weights are set analytically (not trained) so the model is a deterministic
detector of three "instruction" byte bigrams:

    0f 05   syscall
    cd 80   int 0x80
    0f 34   sysenter

The attention looks one token back (relative-position bias favours offset 1),
so the feed-forward can AND "previous byte" with "current byte" and fire on the
bigram. Everything else scores benign.

Run this script to (re)generate model.h. It also self-checks the forward pass
against a brute-force "contains forbidden bigram?" oracle.
"""

import os
import random
import numpy as np

D = 8          # model dim
H = 3          # ffn hidden dim
W = 4          # causal attention window (look back up to W tokens)
SCALE = 1.0    # attention score scale
ATTN_BIG = 30.0

# byte-feature indices inside the embedding
F_0F = 0   # byte == 0x0f
F_CD = 1   # byte == 0xcd
F_05 = 2   # byte == 0x05
F_80 = 3   # byte == 0x80
F_34 = 4   # byte == 0x34

FORBIDDEN = {(0x0f, 0x05), (0xcd, 0x80), (0x0f, 0x34)}


def build_weights():
    E = np.zeros((256, D), dtype=np.float64)
    E[0x0f, F_0F] = 1.0
    E[0xcd, F_CD] = 1.0
    E[0x05, F_05] = 1.0
    E[0x80, F_80] = 1.0
    E[0x34, F_34] = 1.0

    # content contributes a little; relative position dominates.
    Wq = np.eye(D, dtype=np.float64)
    Wk = np.eye(D, dtype=np.float64)
    Wv = np.eye(D, dtype=np.float64)

    # relbias indexed by (i - j), offsets 0..W. Favour offset 1 (previous byte).
    relbias = np.full(W + 1, -ATTN_BIG, dtype=np.float64)
    relbias[1] = +ATTN_BIG

    # ffn: three AND-gates over concat(x, ctx).
    #   h0 = relu( x[05] + ctx[0f] - 1 )  -> bigram 0f 05
    #   h1 = relu( x[80] + ctx[cd] - 1 )  -> bigram cd 80
    #   h2 = relu( x[34] + ctx[0f] - 1 )  -> bigram 0f 34
    W1 = np.zeros((H, 2 * D), dtype=np.float64)
    W1[0, F_05] = 1.0
    W1[0, D + F_0F] = 1.0
    W1[1, F_80] = 1.0
    W1[1, D + F_CD] = 1.0
    W1[2, F_34] = 1.0
    W1[2, D + F_0F] = 1.0
    b1 = np.full(H, -1.0, dtype=np.float64)

    # any gate that fires pushes the logit well above zero.
    w2 = np.full(H, 30.0, dtype=np.float64)
    b2 = -10.0

    return dict(E=E, Wq=Wq, Wk=Wk, Wv=Wv, relbias=relbias, W1=W1, b1=b1, w2=w2, b2=b2)


def softmax(v):
    m = np.max(v)
    e = np.exp(v - m)
    return e / np.sum(e)


def forward(P, data):
    """Reference forward pass; returns the scalar score."""
    toks = list(data)
    n = len(toks)
    if n == 0:
        return P["b2"]  # empty input: no gate fires
    x = P["E"][toks]                       # (n, D)
    q = x @ P["Wq"].T
    k = x @ P["Wk"].T
    v = x @ P["Wv"].T
    best = -1e30
    for i in range(n):
        lo = max(0, i - W)
        js = list(range(lo, i + 1))
        scores = np.array(
            [SCALE * float(q[i] @ k[j]) + P["relbias"][i - j] for j in js],
            dtype=np.float64,
        )
        a = softmax(scores)
        ctx = np.zeros(D, dtype=np.float64)
        for w, j in zip(a, js):
            ctx += w * v[j]
        z = np.concatenate([x[i], ctx])
        h = np.maximum(P["W1"] @ z + P["b1"], 0.0)
        logit = float(P["w2"] @ h + P["b2"])
        if logit > best:
            best = logit
    return best


def oracle(data):
    for a, b in zip(data, data[1:]):
        if (a, b) in FORBIDDEN:
            return True
    return False


def selftest(P, rounds=4000):
    rng = random.Random(1337)
    hot = [0x0f, 0x05, 0xcd, 0x80, 0x34, 0x90, 0x00, 0x41]
    mismatches = 0
    pos_margin = 1e30
    neg_margin = -1e30
    for _ in range(rounds):
        n = rng.randint(0, 48)
        # bias toward the interesting bytes so both classes are well sampled
        buf = bytes(rng.choice(hot) if rng.random() < 0.55 else rng.randint(0, 255)
                    for _ in range(n))
        sc = forward(P, buf)
        mal = sc > 0.0
        exp = oracle(buf)
        if mal != exp:
            mismatches += 1
        if exp:
            pos_margin = min(pos_margin, sc)
        else:
            neg_margin = max(neg_margin, sc)
    return mismatches, pos_margin, neg_margin


def c_array_2d(name, A):
    rows, cols = A.shape
    out = [f"static const float {name}[{rows}][{cols}] = {{"]
    for r in range(rows):
        vals = ", ".join(f"{A[r, c]:.1f}f" for c in range(cols))
        out.append(f"    {{{vals}}},")
    out.append("};")
    return "\n".join(out)


def c_array_1d(name, v):
    vals = ", ".join(f"{x:.1f}f" for x in v)
    return f"static const float {name}[{len(v)}] = {{{vals}}};"


def emit_header(P, path):
    lines = []
    lines.append("/* model.h -- GENERATED by gen_model.py. Do not edit by hand. */")
    lines.append("/* Tiny byte-level transformer guard for filter. */")
    lines.append("#ifndef MODEL_H")
    lines.append("#define MODEL_H")
    lines.append("")
    lines.append(f"#define GUARD_D {D}")
    lines.append(f"#define GUARD_H {H}")
    lines.append(f"#define GUARD_W {W}")
    lines.append(f"#define GUARD_SCALE {SCALE:.1f}f")
    lines.append("")
    lines.append(c_array_2d("GUARD_E", P["E"]))
    lines.append(c_array_2d("GUARD_WQ", P["Wq"]))
    lines.append(c_array_2d("GUARD_WK", P["Wk"]))
    lines.append(c_array_2d("GUARD_WV", P["Wv"]))
    lines.append(c_array_1d("GUARD_RELBIAS", P["relbias"]))
    lines.append(c_array_2d("GUARD_W1", P["W1"]))
    lines.append(c_array_1d("GUARD_B1", P["b1"]))
    lines.append(c_array_1d("GUARD_W2", P["w2"]))
    lines.append(f"static const float GUARD_B2 = {P['b2']:.1f}f;")
    lines.append("")
    lines.append("#endif /* MODEL_H */")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    P = build_weights()
    miss, pmar, nmar = selftest(P)
    print(f"[selftest] mismatches={miss}  min positive score={pmar:.3f}  "
          f"max negative score={nmar:.3f}")
    assert miss == 0, "model does not match the forbidden-bigram oracle!"
    assert pmar > 1.0 and nmar < -1.0, "decision margin too small"
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "model.h")
    emit_header(P, out)
    print(f"[ok] wrote {out}")


if __name__ == "__main__":
    main()
