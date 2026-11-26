# El Clásico: a forged Groth16 proof via an exceptional GLV addition

[Русская версия](solve.ru.md)

## 1. Challenge and circuit

The organizer distributed `challenge.go`, a Groth16 proving key (`pk.hex`), verifying key (`vk.hex`), and a Go module pinned to **gnark v0.16.4** and **gnark-crypto v0.21.1-0.20260913224755-1409fe592052**. The circuit uses BN254 as the proving system's scalar field while **emulating BLS12-381** G1 point and scalar arithmetic.

`GET /challenge` returns a server-generated target `A=(a,b)`. The submission consists of decimal `s`, `p_x`, `p_y` and `proof_hex`. The server restricts `s` to `0 < s < r` and **`s % 9 != 0`**, validates public witness encoding, then calls the original Groth16 verifier.

The important source fragment is:

```go
curve.AssertIsOnCurve(&c.P)
curve.AssertIsInSubgroup(&c.P)
q := curve.ScalarMul(&c.P, &c.S)
dx := fp.Sub(&q.X, &c.A.X)
dy := fp.Add(&q.Y, &c.A.Y)
fp.AssertIsDifferent(&q.X, &c.A.X)
fp.AssertIsEqual(fp.Mul(fp.Mul(dx, dx), dx), fp.Mul(dy, dy))
```

Thus the circuit checks

```text
Q = [s]P
Q.x != a
(Q.x - a)^3 == (Q.y + b)^2 mod p
```

The target `A` itself is a genuine random BLS12-381 G1 point. Satisfying this “impossible” equation requires exploiting the *circuit gadget*, not falsifying the BN254 pairing verification.

## 2. The unsound part of classic GLV

The actual `Curve.ScalarMul` path for the non-constant point on these BLS12-381 parameters was **`scalarMulGLV`** (classic GLV), rather than the fake-GLV variant. The classic path lacks a final on-curve/subgroup assertion on the computed result.

GLV decomposes the scalar into two approximately 128-bit signed pieces:

```text
[s]P = [s1]P + [s2]Φ(P)
Φ(x,y) = (ωx, y)
s1 + λ*s2 ≡ s (mod r)
λ² + λ + 1 ≡ 0 (mod r)
```

The circuit verifies the subscalar bit ranges and their **linear congruence**, but does not require that the hint returns the *canonical lattice decomposition*. A prover can replace the decomposition hint, provided those checks pass.

Arithmetic then uses incomplete `add`, `double`, `doubleAndAdd` and `triple` formulas plus a slope `ratioHint`. At a coincident-point addition, numerator and denominator are both zero. The constraint proving a slope is valid reduces to:

```text
slope * 0 == 0
```

Every slope satisfies it. A malicious prover can select a non-geometric slope without violating that constraint, obtaining an off-curve result.

## 3. Why the first proposed parameters failed

The initial solution in the earlier ChatGPT session asserted that `s1=2λ+9`, `s2=5` would create an exceptional addition. **This was a hypothesis, not the successful attack.** The agent's exact simulator found no `0/0` for that pair.

Two further modeling errors were fixed during the investigation:

- A sign error in `doubleAndAdd`: the correct expression is `y3 = l2*(x3-x1)-y1`, not a version with the opposite sign.
- An incorrect accumulator invariant, which initially produced wrong parity/mod-16 lattice constraints. The corrected last-window coefficients are `4u+2` and `4v+2`, where `u=s1>>3` and `v=s2>>3`.

A `ccs.Solve`-only diagnostic failed with a commitment placeholder error. A separate `CheckCircuit` in `scripts/probe/probe.go`, using full Groth16 Setup/Prove/Verify, logged native hint calls. The corrected Python simulator was checked against **140 actual ratioHint events** and benign scalar decompositions such as `(12345,6789)`.

## 4. Force a collision using the GLV lattice

The lattice of legal decomposition differences is

```text
L = {(x,y) : x + λ*y ≡ 0 (mod r)}.
```

Consider the final merged GLV window `i=2`. Let `u=s1>>3` and `v=s2>>3`. The doubled accumulator has coefficients `(4u+2, 4v+2)` in the signed bases `P` and `Φ(P)`. The lookup-table point has coefficients `(d_Q,d_Φ)` in `{-3,-1,1,3}`. If they coincide, then:

```text
(4u+2-d_Q) + λ*(4v+2-d_Φ) ≡ 0 (mod r).
```

So we need a short vector `(x,y)` in `L`, satisfying the window's congruence classes. A Gaussian-reduced lattice search finds the particularly simple vector:

```text
(x,y) = (λ,-1)
λ = 228988810152649578064853576960394133503
d_Q = -1
d_Φ = 3
u = 57247202538162394516213394240098533375
v = 0
s1 = 457977620305299156129707153920788267003 = 2λ - 3
s2 = 7
sign1 = sign2 = 0
s  = 2060899291373846202583682192643547201524
s % 9 = 6
```

`s1` is 129 bits and `s2` is 3 bits: both are legal under the used hint checks. The congruence `s1 + λ*s2 ≡ s (mod r)` holds. At `i=2`, the accumulator equals the point being added, forcing a `0/0` chord-slope hint.

## 5. Choose the fake slope for the server's target

With the collision point `(x0,y0)` and free slope `m`, set `z=m²-3x0`. The forged output after `doubleAndAdd` can be written

```text
x3 = NX/z²
NX = 4*m*y0*z + 4*y0² + x0*z²
y3 = NY/z³
NY = (m*z+2*y0)*(4*m*y0*z+4*y0²) - y0*z³
```

To satisfy the server's condition, clear denominators:

```text
(NX-a*z²)^3 - (NY+b*z³)^2 = 0 mod p
```

This produces a polynomial in `m` of degree **12**. The saved search reported **five roots** modulo the BLS12-381 base field. A valid root is:

```text
m = 3779855849564975014954687576048500261310875051983363318127962126203533956438461724178333382126496049625088006452104
```

The resulting `Q` is **not on the BLS12-381 curve**, but satisfies `Q.x != a` and `(Q.x-a)^3=(Q.y+b)^2` modulo `p`. The second denominator `z` is nonzero, so later constraints remain satisfiable.

## 6. Generate the forged Groth16 witness

The saved working script is `scripts/solver_main.go` inside the private agent archive, used as `solver/main.go` inside the handout Go module. It overrides two gnark hint implementations while keeping the original R1CS and the organizer's keys:

| Hint | ID (FNV-1a of qualified name) | Override |
| --- | ---: | --- |
| `decomposeScalarG1` | `1139094758` | output chosen `s1=2λ-3`, `s2=7`, signs zero |
| `ratioHint` | `3763044065` | for `num=den=0`, output selected `m`; otherwise compute honest `num/den` |

The executable first checks that the live `GET /challenge` target equals the saved `target_a,target_b`, avoiding proofs for a changed instance. It then compiles the **same circuit**, loads `pk.hex` and `vk.hex`, constructs a public witness and proves with `backend.WithSolverOptions(solver.OverrideHint(...))`. No verifier patching, key substitution or server modification is involved.

The preserved successful run states:

```text
nbConstraints=192987
hint ids: decomp=1139094758 ratio=3763044065
local Groth16 verify OK
HTTP 200: Flag is: gctf{...}
```

The server accepted the serialized `proof_hex` with the original verifying key and returned the flag. The literal flag appears in the **private** `evidence/solve_run.log` and `FLAG.txt`; it is intentionally masked here.

## 7. Evidence and independent checks

The agent's `glacierctf_el-clasico_materials.zip` includes the original **46,348,852-byte** handout archive, `REPORT.md`, the real `solver_main.go`, Python search/simulator programs, `params.json`, `target.json`, the original submitted request, numerical checks and the saved successful run.

Offline [scripts/verify_parameters.py](scripts/verify_parameters.py) independently evaluates the public scalar congruence, the last-window lattice identity, the recorded off-curve `Q` and the challenge equation. This is **not** a substitute for a Groth16 verifier: the actual Groth16 proof was verified during the recorded historical run.

The original chat contained the early mathematical proposal and instructions for the bridge. The later agent report corrects its proposed `s1/s2` and documents the final successful proof. The two accounts describe **different phases** of the same solution.

## 8. Lesson

A cryptographic circuit is only sound if all intermediate operations are adequately constrained. A hint is not trusted merely because its output satisfies a weaker congruence. Incomplete elliptic-curve formulas can expose unconstrained witnesses in exceptional cases; this allowed an off-curve “scalar multiplication” result and a formally valid proof of the challenge equation.
