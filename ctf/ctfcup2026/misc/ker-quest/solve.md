# ker quest: the full path through the layers

[По-русски](solve.ru.md)

## 0. What I was trying to do

The task was not one cipher to break. It was a sequence: solve the current artifact, use its result as a password for the next one, and continue until a flag appeared. The practical rule was to verify each archive decryption before treating any candidate phrase as correct.

## 1. The year in task 1

The file `task1/desc.md` asked for the year of the author's first CTF. The answer was `2015`. It opened `task2.zip`, and the AES-ZIP authentication check passed.

This matters because an unzip command merely returning bytes is not good enough evidence with encrypted archives. A successful HMAC check is evidence that the password and ciphertext agree.

## 2. Fixing an image that appeared to end too early

The `task2/task.png` image had an invalid IHDR height. The header said **1152**, but the CRC matched a height of **1497**. The missing 345 rows were not gone from the compressed image stream, they were hidden by the incorrect metadata.

PNG stores height as a big-endian four-byte integer at offsets 20 to 23. I changed those four bytes to `00 00 05 d9` and kept the rest of the file intact.

```python
from pathlib import Path
import struct

png = bytearray(Path("task2/task.png").read_bytes())
assert png[1:4] == b"PNG"
png[20:24] = struct.pack(">I", 1497)
Path("task2_restored.png").write_bytes(png)
```

Opening the restored image revealed the next phrase, `myfirstsolvedforensicstask2016`. It decrypted `task3.zip`, again with a valid archive HMAC. The fact that a simple four-byte repair recovered a readable message is much more informative than trying random image carving.

## 3. Reassembling the jigsaw

`task3/puzzle.png` contained roughly sixty displaced fragments, with red lettering on light backgrounds. The objective was to reconstruct the phrase used as the next archive password.

The working approach was:

1. Separate fragments from the white background.
2. Compare edges and nearby red pixels, not only the outside bounding boxes.
3. Score plausible joins using color continuity and contour shapes.
4. Merge reliable clusters and read the remaining letters manually.
5. Verify the complete phrase against the encrypted archive.

Several attractive sentence guesses failed. In particular, partial alignments were not enough to claim a password. The experimental tools were named `edge_match.py`, `score_joins.py`, `check_joins.py`, `contour_votes.py`, `assemble.py` and `merge_clusters.py`.

The confirmed password was `takearesttraveller`, which opened `task4.zip`. Those intermediary images and scripts existed in the research handoff, but only the password validation, not every proposed image join, is part of the final proof.

## 4. Reverse engineering a MIPS crackme

The next stage contained a MIPS crackme. I treated it as a validation program, not as something to brute force blindly. The recorded analysis reduced its checks to a system of 22 constraints. Solving the constraints and checking the answer against the archive gave:

```text
easyreverseforeveryone
```

That was the password for `task5.zip`. The original instruction trace and exact 22 equations were not preserved in the surviving handoff, so I cannot honestly reproduce each equation here. The confirmed property is the password that successfully authenticated the next archive.

## 5. Audio and a RAR5 password

The next audio clue encoded a sequence of words using the NATO spelling alphabet. Transcribing the words and mapping them back to letters and digits revealed:

```text
th3r34l0n3p4ssw0rd1337
```

This unlocked the RAR5 material and exposed the later task files. The important distinction is that the spoken spelling was the carrier for a password, rather than a message to submit directly as a flag.

## 6. An image, Base64 and an executable

Another stage used green characters in an image as Base64 data. After decoding, a second Base64-encoded image and an ELF program appeared in the chain. Inspecting that executable supplied the phrase:

```text
ohnicetoseeyouin2036year
```

The password authenticated both files in `task7.zip`. This is where checking file signatures after every decoding step mattered: Base64 can contain any bytes, so I used the resulting format to decide whether to view an image or analyze a program.

## 7. Finding one useful packet among 10,000

The network capture contained about **10,000 UDP packets**. The relevant item was packet number **1337**, a deliberate clue that also served as a ZIP password. Extracting that packet's content and opening the nested material gave:

```text
glinomesieforever
```

That phrase opened `task8.zip`. An important reproduction detail is whether packet numbering in a script starts at zero or one; it is safer to verify the chosen packet's contents instead of trusting an array index.

## 8. The location and the obfuscated JavaScript

A later OSINT clue featured `EBIHARA`. Following the location context to **Super Hotel Premier Akihabara** produced the password:

```text
superhotelpremierakihabara
```

The following stage involved obfuscated JavaScript. Reading its resulting constant rather than attempting unrelated ZIP guesses produced:

```text
junkjunkjunkjunkcode
```

This authenticated the final archive.

## 9. The last layer is ROT13

The extracted `flag.txt` did not initially begin with `ctfcup{`. It was ROT13 text, with an encoded prefix `pgsphc{`. Applying ROT13 to the contents converted it into the accepted flag format.

```python
from pathlib import Path
import codecs

raw = Path("flag.txt").read_text().strip()
plain = codecs.decode(raw, "rot_13")
assert plain.startswith("ctfcup{") and plain.endswith("}")
print("ctfcup{...}")  # Do not publish the full flag
```

The original recovered flag is deliberately masked in this public repository.

## 10. What made this challenge difficult

No single step was especially exotic. The difficulty came from knowing when to change tools: PNG metadata instead of steganography, real jigsaw reconstruction instead of password guessing, MIPS constraints instead of strings alone, phonetic transcription instead of audio brute force, packet indexing instead of reading the entire capture, and a simple ROT13 after the last archive.

A reliable check after each layer prevented an early mistake from contaminating the next ten steps. The team scoreboard recorded the accepted solve on October 31, 2026.
