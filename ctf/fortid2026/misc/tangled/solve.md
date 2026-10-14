# Tangled - writeup (two Factorio blueprints hiding two QR codes)

## Getting to know the task

The description:

> An abandoned factory hides a message within its tangled machinery. Untangle the construction and uncover the secret.

Two text files are given. Each contains one very long line starting with `0eN...` - letters, digits, pluses, slashes. There is no visible `FortID{...}`.

Goal: recover the hidden flag from these two files.

## The first question: what format is this?

A random-looking string could be a cipher, but before brute-forcing anything I looked at the structure. Two observations:

1. The line starts with a single `0` followed by a long Base64-looking body (`eN...` - the `eN` prefix is the classic signature of a **zlib-compressed stream**, whose first bytes are `0x78 0x9C`, visible as `eJw` / `eN` after Base64).
2. The task mentions a "factory" ("abandoned factory", "tangled machinery").

I know that the game **Factorio** exports its blueprints as exactly such strings: `0` + base64( zlib( JSON ) ). I googled "factorio blueprint string format" to confirm, and this is the documented format.

### Why decode it at all

The principle: this file is *text that contains other data*, so before treating the characters as a cipher I should ask "what is this text actually made of?" The structure (`0` + Base64 body) and the `eN` zlib signature tell me the characters are just an **encoding layer** - the real content is the binary stream underneath. Decoding layers that are known encodings costs nothing and gives me the actual payload to inspect. So I decided to try decoding because: the format signature was recognizable, and the decompressed JSON would tell me what the challenge is really about (a factory description - entities, tiles, wires - instead of letters).

### Decoding the blueprint

```python
import base64, zlib, json

s = open('handout_file_1.txt', encoding='utf-8').read().strip()
assert s.startswith('0')
raw = zlib.decompress(base64.b64decode(s[1:]))
data = json.loads(raw)
print(data['blueprint'].keys())
# dict_keys(['icons', 'entities', 'wires', 'tiles', 'item', 'version'])
```

The JSON is structured: `entities` (machines, belts, inserters...), `wires` (connections), `tiles` (ground tiles). The coordinates are in `entity["position"]["x"/"y"]` and `tile["position"]["x"/"y"]`.

### Why I thought the flag is in the geometry

The decoded blueprint is a description *of positions*: every machine and every tile has coordinates. A flag could be hidden in the plain text fields (none found), in the wires (they look like a graph - maybe a puzzle), or in the **geometry**. I did not know yet which one. So I formed a hypothesis: *the message may be drawn by the positions/types of objects*, and I decided to check it by rendering the objects as an image - if the hypothesis is wrong, the render will just look like noise, and I will look elsewhere. (This is the "abandoned factory hides a message within its tangled machinery" hint taken literally: the machinery itself is the medium.)

## Step 2. First blueprint: the tile types draw a QR code

### Where the "counts" come from

The counts are not given - the script printed them. After decoding, I asked the JSON simple questions with a few lines of Python (`len(bp1['entities'])`, `len(bp1.get('wires', []))`, `len(bp1['tiles'])`), and the output was:

- `entities` = 290, `wires` = 104, `tiles` = 841.

### How I realized it is a QR code (not a guess)

The decisive step was arithmetic on that number: **841 = 29 x 29** - a perfect square. A perfect square is the signature of a square bit matrix, and in CTF contexts the most common square bit matrix that is meant to be *read* is a **QR code**. Moreover, 29 modules is exactly the size of QR **version 3** (the QR sizes are 21x21 for v1, 25x25 for v2, 29x29 for v3 - a well-known table). So the hypothesis became concrete: the tiles are arranged as a 29x29 QR code.

To make it testable I needed a binary value per cell. The tiles have two different names:

```python
from collections import Counter
print(Counter(t['name'] for t in bp1['tiles']))
# landfill 429, foundation 412  (429+412 = 841)
```

So the 29x29 square is fully covered, but by two different materials. That gives exactly the two values a bit matrix needs: `landfill` as black, `foundation` as white. (Why this two-material split is meaningful: a QR code is by definition a pattern of black/white modules - so a fully-covered square made of two distinct tile types is structurally exactly that.)

Why this step gets us closer: we now have a concrete, checkable candidate - a 29x29 binary matrix that should decode as a QR code. If it decodes, we have real content; if not, we fall back to other hypotheses.

### Building and reading the first QR

The tile coordinates are `x` in -69..-41 and `y` in -5..23 - 29 columns and 29 rows. To index a Python matrix I shift them to start at 0:

```python
col = int(t['position']['x']) + 69
row = int(t['position']['y']) + 5
mask1[row, col] = 1   # if the tile is landfill
```

Then render it the way QR decoders like it: a white quiet zone of 4 modules around it, black modules as pixel 0, white as 255, scaled up with nearest-neighbor (no smoothing):

```python
padded = np.pad(mask1, 4, constant_values=0)
image = ((1 - padded) * 255).astype(np.uint8)
image = cv2.resize(image, None, fx=16, fy=16, interpolation=cv2.INTER_NEAREST)
text, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
```

The decoder returns:

```text
FortID{Wh3n_7h3_8e1ts_b3c0me
```

We got the flag prefix `FortID{` - the QR hypothesis is right. But the string has no closing `}` - this is only the first part.

## Step 3. Second blueprint: 25x25, but not a QR by itself

The second file has only `tiles`, all `landfill`, 330 of them, spanning `x` -12..12, `y` -12..12 - a **25 x 25** field (QR version 2 size). Drawing it directly does NOT decode: no proper finder patterns, looks noisy.

But the first file also has 290 entities, and their coordinates span `x` -66.5..-42.5, `y` -2.5..21.5 - also a **25 x 25** field, just at different absolute coordinates. So we have two binary layers of the same size:

- `A[y, x] = 1` if there is any entity of blueprint 1 at that relative position;
- `B[y, x] = 1` if there is a landfill tile of blueprint 2 at that relative position.

Neither alone is a readable QR. The idea: the two layers are two halves of one construction, and combining them reveals the code.

Why this step gets us closer: we found the second piece of the puzzle - a second 25x25 bit matrix hiding in the entity geometry of file 1 and the tile geometry of file 2.

## Step 4. XOR the two masks

Why XOR: if an object in one layer "flips" the cell of the other, then a black module appears where exactly one layer has a mark, and cells marked in both cancel out.

```python
A = np.zeros((25, 25), dtype=np.uint8)
for e in bp1['entities']:
    A[round(e['position']['y'] + 2.5), round(e['position']['x'] + 66.5)] = 1
B = np.zeros((25, 25), dtype=np.uint8)
for t in bp2['tiles']:
    B[int(t['position']['y'] + 12), int(t['position']['x'] + 12)] = 1
qr2 = A ^ B
```

Counts: A has 290 marks, B has 330, they overlap in 144 cells, so the XOR has `290 + 330 - 2*144 = 332` black modules. The overlap is exactly where the two layers agree - and those cancel out.

Rendering the XOR the same way and decoding:

```text
_5p49hett1_tr41n5_8ec0m3_
```

The second part is recovered from the two files, no character guessing.

Why this step gets us closer: the two fragments now together form the flag text.

## Step 5. Assembling the flag

Two independently recovered fragments:

```text
QR 1: FortID{Wh3n_7h3_8e1ts_b3c0me
QR 2: _5p49hett1_tr41n5_8ec0m3_
```

A literal concatenation gives an unclosed string with a trailing `_`. The meaningful text drops that last underscore and closes with `}`:

```python
candidate = part1 + part2.rstrip('_') + '}'
```

The resulting flag (thematically a wink at Factorio: "When the belts become spaghetti, trains become...") was entered on the platform and accepted - confirmed.

## Verification

The solver `scripts/solve.py` reproduces the whole chain from the two original files (already run - output matches exactly): 29x29 QR with the first fragment, 25x25 XOR QR with the second, both decoded by OpenCV's QRCodeDetector. The evidence images are saved under `evidence/`.

## Dead ends (what I tried first)

1. **Looking for plaintext inside the Base64 string.** The compressed binary stream contains no readable flag; the encoding and compression layers must be removed first, and even then the message is geometric, not textual.
2. **Studying the wires, combinators, and numeric conditions of blueprint 1.** It has 104 connections and lots of settings; they look like a cipher or a logic puzzle. They turned out unnecessary - only tile types and entity/tile coordinates matter.
3. **Drawing all 841 tiles the same color.** Then no QR appears - the first QR requires distinguishing `landfill` from `foundation`.
4. **Reading the second file directly.** 330 landfill cells alone do not form a readable QR.
5. **Overlaying the two blueprints by absolute coordinates.** They are different map areas; each mask must be normalized to its own bounding box first. Only then do both become 25x25.
6. **Using OR instead of XOR.** With OR the 144 shared cells stay black and the QR stays noisy; XOR cancels the agreement and reveals the code.
7. **Omitting the quiet zone or smoothing pixels.** QR decoders lose the matrix edges; a 4-module white border and nearest-neighbor scaling make decoding stable.

## One-paragraph version

Tangled gives two Factorio blueprint exports (strings `0` + base64 + zlib + JSON). Blueprint 1's 841 ground tiles split into landfill and foundation exactly as a 29x29 QR code (841 = 29^2), which decodes to the first half of the flag. Its 290 entities and blueprint 2's 330 landfill tiles both occupy 25x25 grids; XORing the two normalized masks cancels the 144 shared cells and reveals a second 25x25 QR with the second half. Concatenating the fragments (dropping the trailing underscore of the second QR and closing with `}`) gives the flag, which was accepted on the platform.

## Lessons learned

1. A string that looks like ciphertext may be an exported structured file - identify the format before trying ciphers.
2. Perfect squares in CTF counts (841 = 29^2, 625 = 25^2) are strong hints of a square bit matrix / QR code.
3. Information can hide not in values but in object types and coordinates.
4. Two same-sized spatial layers should be checked with bitwise combinations, especially XOR.
5. Reproducible extraction beats guessing: both hidden strings come from the original data through an independent QR decoder.

## Files

- `assets/handout_file_1.txt`, `assets/handout_file_2.txt` - the challenge files
- `scripts/solve.py` - reproducible solver: `python3 solve.py file1 file2 --out <dir>`
- `evidence/` - the rendered masks and QR images, `decoded_fragments.txt`