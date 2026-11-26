# Flagpression — solving notes

[Русская версия](solve.ru.md)

## 1. Evidence and starting point

The task card supplies one long decimal fraction and an image of a handwritten model. The retained transcription of the decimal is:

```text
0.0009765685210667505247445943812195496662085124682194321976804631657536092783226825644989323235755887661109166439942109622745804097388690562405020642704789717348288883285073879761368008154696359582503489
```

The handwritten note was interpreted as a uniform arithmetic-coding model with a 33-character ordered alphabet:

```text
gctf{n0T?har8_pYwms391A4E2y6ZSB7}
```

The order is significant. In arithmetic coding, rearranging the alphabet changes every subsequent interval. The small dash in the handwritten notation was read as an underscore.

The notes also mention a *Transformer*, motivating investigation of whether the displayed number was produced with a particular sequence of floating-point interval updates instead of exact base-33 arithmetic.

## 2. What arithmetic decoding would normally do

With a uniform alphabet of size 33, divide the current interval into 33 equally wide sections. Given a fractional value `x`, the idealized high-precision decoder repeatedly computes:

```python
index = int(x * 33)
character = alphabet[index]
x = x * 33 - index
```

The first decoded characters are `gctf{`, matching the flag format. This is a useful sign that the ordered symbols and arithmetic-coding interpretation are meaningful.

**But a correct-looking prefix is not proof of a correct decoder.** Directly reading the supplied decimal as an exact rational number and applying this recurrence does **not** produce the accepted 64-character flag. It diverges at character index 12 (zero-based): the direct decoder gives `_` where the accepted candidate contains `a`. It later produces multiple internal braces.

The supplied decimal has 202 digits after the point. For the exact rational interpretation, decoding both sides of the rounding interval (± half of the final decimal unit) establishes 132 common characters, but that stable text is a *different* sequence. The previously saved `flagpression_solve_verified.py` correctly tests stability under this **particular numerical model**, not the correctness of the intended challenge decoding.

## 3. The numerical-implementation trap

During the original investigation, the mismatch prompted inspection of how interval boundaries were constructed. The historical chat records the working hypothesis that boundaries were accumulated by adding `1/33` successively in a `float` representation rather than computing exact rational boundaries. In general, these are **not equivalent** because floating-point addition introduces rounding at every step.

For example, constructing boundaries as `0, 1/33, 2/33, ...` using repeated binary floating-point additions can produce different values from calculating `i/33` in high precision. Even a very small interval discrepancy can change a decoded character after several iterations.

The original chat reported a reverse-encoding check against the decimal. However, the exact historical encoder and its full reproduction log are not preserved in the available materials. Consequently, this write-up does **not** claim that the included exact-arithmetic checker reproduces the final flag, or that the specific historical floating-point transform was independently established from the remaining files.

## 4. Recovery and accepted result

The earlier session proposed a 64-character flag with a readable, leetspeak-style section about compressors and three literal question-mark characters. The participant independently resubmitted that **exact** candidate to the GlacierCTF platform on November 26, 2026 and confirmed acceptance.

The accepted flag is intentionally masked here:

```text
gctf{...}
```

The explicit acceptance is stronger evidence for the final result than a standalone decoder that follows a different numerical convention. We preserve the distinction between the proven submission and the unproven exact reconstruction of the challenge's encoder.

## 5. Inspecting the preserved data

Run the included **non-network** arithmetic sanity checker:

```sh
python3 scripts/inspect_decimal.py
```

It uses Python's `fractions.Fraction`, prints the first divergence between exact base-33 decoding and the accepted (locally redacted) candidate, and reports the number of characters insensitive to ± half a unit in the displayed last decimal digit. No remote connection or challenge binary is required.

This is diagnostic evidence, **not** a replacement for the original competition solver. We intentionally do not publish an invented implementation of the missing transform.

## 6. Lessons

- Arithmetic coding depends on the *ordered* alphabet, probability model and interval-update convention.
- A plausible flag prefix can survive a model mismatch for several symbols.
- Arbitrary-precision rational arithmetic is not always interchangeable with a deliberately rounded floating-point implementation.
- A verified platform submission and a reproducible derivation are different kinds of evidence; record both honestly.

## 7. Preservation limits

The preserved library contains the early decoder and a later exact-rational stability checker, both documented as diagnostic rather than successful full solvers. The original challenge photograph, the final working encoder implementation, and raw platform acceptance response are not included. Platform acceptance was reconfirmed directly by the participant and is recorded as such, without inventing logs.
