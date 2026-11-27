# Flag on the flag: solving path

[Русская версия](solve.ru.md)

## 0. Plan

The wording points directly at the team's physical flag. This is a classic OSINT task: identify the object, find higher-quality public photographs, inspect details that are unreadable in the challenge view, then decode whatever hidden data the object carries.

The successful path was:

1. Identify the purple LosFuzzys banner in public team photos and presentations.
2. Zoom in on the long hexadecimal-looking string printed along the bottom edge.
3. Group the characters into bytes.
4. XOR every byte with `0x42`.
5. Use the required `LosFuzzys{...}` format to recognize the plaintext.
6. Revisit photographs because the first candidate was one character short.
7. Recover the byte hidden by a fabric fold and submit the complete flag.

## 1. Finding the same banner elsewhere

The useful visual target is a purple LosFuzzys team banner. Public LosFuzzys material shows the same object: the center carries the large team name, the upper edge contains the old `hack.more.systems` branding, and a long hex-like sequence runs near the lower edge.

A particularly useful independent source is the LosFuzzys beginner-training presentation *Introduction to Capture the Flag Events* (Winter Semester 2025). Its team-presentation page contains a group photo holding this banner. Public event photographs and material around Insomni'hack were also searched during the solve.

The point of these extra sources was not to identify a person. It was to obtain a clearer angle on the printed bytes and, especially, the section hidden by folds in one photo.

## 2. Recognizing the encoding

The lower banner text looks like hexadecimal rather than ordinary prose. Reading it as byte pairs gives a byte stream.

The retained solve notes record the transformation as:

```text
plain_byte = encoded_byte XOR 0x42
```

Applying the same constant to every byte quickly produces readable ASCII and the expected `LosFuzzys{...` prefix, which is a strong confirmation that both the byte grouping and the XOR key are correct.

A minimal decoder is included in [scripts/decode_banner.py](scripts/decode_banner.py):

```sh
python3 scripts/decode_banner.py <hex-bytes-without-spaces>
```

It accepts spaces and punctuation too, strips non-hex separators, converts byte pairs and XORs each with `0x42`.

## 3. Why the first answer was wrong

The first readable reconstruction ended with the inner text:

```text
y0lO
```

That produced a syntactically plausible flag, but the platform rejected it.

The failure mattered: the decoding method was already consistent, so the next question was not "find a different cipher" but "are we missing bytes from the photograph?"

Looking again at the physical banner showed exactly that problem. The lower edge is not flat in the available photos; a fold hides the next printed byte.

## 4. Recovering the hidden final character

The missing encoded byte was reconstructed as hexadecimal `63`. With the already-established key:

```text
0x63 XOR 0x42 = 0x21
0x21 = '!'
```

So the visible `y0lO` suffix gains one final exclamation mark.

For reference, the five plaintext bytes `y0lO!` correspond under this XOR to:

```text
3b 72 2e 0d 63
```

The first four are merely the bytewise encoding of the already-readable text; the important OSINT recovery was the last `63`, hidden by the fold.

## 5. Result

The corrected flag had the form:

```text
LosFuzzys{y0lO!}
```

It was accepted by the GlacierCTF platform during the competition. The repository masks the actual flag elsewhere according to the write-up policy.

## 6. What made the task tricky

The cryptography is intentionally tiny. XOR by one byte is not the real obstacle. The OSINT difficulty is obtaining a photograph where the banner's bottom edge is readable and not treating a visually truncated byte sequence as complete.

The useful workflow is therefore:

- use the challenge image only as a starting point;
- search for the same physical object in official/team-controlled public material;
- compare multiple angles;
- decode only after transcription;
- when a cleanly decoded candidate is rejected, re-check the source image for occlusion before inventing a new cipher.

## 7. Evidence limitations

No original challenge-image file or raw browser session was preserved in the material used for this repository. The accepted result and the intermediate rejected candidate are preserved in the competition conversation. Public LosFuzzys training material independently confirms the appearance of the same purple banner and the presence of the hexadecimal-looking line.

The repository therefore distinguishes the historical solve from the public corroborating source rather than pretending that a raw acquisition log survived.
