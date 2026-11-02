# ker quest: nine layers, one flag

[По-русски](solve.ru.md)

## 0. The shape

This is not one cipher. It is a chain: open layer N, read what it says, use that as the password for layer N+1, repeat.

The chain mixes two container formats, and the difference matters for verification:

- `task2.zip`, `task3.zip`, `task4.zip`, `task5.zip`, `task7.zip`, `task8.zip`, `task9.zip` and the small archives inside the packet capture are WinZip AES-256. Those carry an HMAC over the plaintext, so a wrong password fails loudly and a correct one is cryptographically confirmed. `unzip_aes.py` is the helper that checks it.
- `task6.rar` is RAR5. It has its own password verification, but not the same HMAC construction, so it is opened with `unrar x -p...` and checked by whether extraction completes.

That distinction is why "unzip printed something" is not evidence for the ZIP layers; for RAR5 the successful extraction is the check. Both rules saved us from several attractive wrong passwords.

The layers, in order: a question about a year, a broken PNG header, a jigsaw, a MIPS crackme, a spoken spelling alphabet, an image with Base64, a packet capture among ten thousand decoys, an OSINT photo, obfuscated JavaScript, and finally ROT13.

Tooling that mattered: `unzip_aes.py` for WinZip AES with HMAC verification, Pillow for images, `unrar` for RAR5, `tshark` for the capture.

## 1. Layer 1: the year

`task1/desc.md` asks in Russian for the year of the author's first CTF. The answer `2015` opens `task2.zip` and the HMAC check passes for both entries.

Why this step: an archive that authenticates tells us the password is exactly right, so we can trust it and move on.

## 2. Layer 2: a PNG that ends too early

`task2/task.png` looks short: the image stops mid-sentence. The IHDR says height `1152`, but the CRC of the header matches `1497`.

PNG stores height as a big-endian 32-bit integer at offsets 20 to 23. Writing `0x000005D9` there and leaving the rest untouched recovers the missing 345 rows, because the compressed stream was never truncated; only the metadata lied.

```python
# scripts/restore_ihdr.py
png[20:24] = struct.pack(">I", 1497)
```

The restored image shows `myfirstsolvedforensicstask2016`, which opens `task3.zip`.

Why this step: a four-byte repair is much better evidence than carving random rows out of the IDAT stream.

## 3. Layer 3: a jigsaw

`task3/puzzle.png` holds about sixty displaced fragments with red lettering on a light background. The approach was mechanical, not visual guessing:

1. Segment fragments from the background.
2. Score joins by edge colour continuity and contour shape, not by bounding boxes alone.
3. Merge the confident clusters, then read the remaining letters by eye.
4. Verify the phrase against the archive HMAC.

Scripts `edge_match.py`, `score_joins.py`, `check_joins.py`, `contour_votes.py`, `assemble.py`, `merge_clusters.py` are the iterations of that search. Several plausible sentences failed the HMAC; the one that passed was `takearesttraveller`, and it opened `task4.zip`.

Why this step: only the archive can confirm a reading of a noisy image, so the HMAC is the oracle for the puzzle.

## 4. Layer 4: a MIPS crackme

`task4/crackme` is an ELF 32-bit MSB MIPS, statically linked and stripped. It is not runnable natively here, and it does not need to be: the validation reduces to a system of 22 constraints on the input, which we solved and then tested against the archive.

The accepted password `easyreverseforeveryone` opens `task5.zip`. After this layer, running unknown binaries stopped being tempting at all; the archive HMAC gives the same answer with less risk.

## 5. Layer 5: a spelling alphabet

`task5/task5.wav` contains a voice reading a sequence of NATO spelling words. Transcribing them and taking the first letter of each word gives digits and letters, and the result is a password, not a message:

```text
th3r34l0n3p4ssw0rd1337
```

It unlocks `task6.rar`, which is RAR5, so `unrar x -p...` is needed rather than the Python helper.

Why this step: the audio was a carrier, not a clue; treating it as speech-to-text input would have been the wrong abstraction.

## 6. Layer 6: green characters and an executable

`task6/image1.png` carries green characters that are Base64. Decoding gives a second Base64 image and an ELF; inspecting the executable yields the phrase `ohnicetoseeyouin2036year`, which authenticates both entries of `task7.zip`.

Why this step: Base64 can decode to anything, so the file signature after each step is what decides whether to open an image or analyse a program.

## 7. Layer 7: one packet among ten thousand

`task7/challenge.pcap` holds exactly 10 000 UDP packets. Each payload starts with a four-byte index and then a small ZIP containing a 31 to 34 byte `flag.txt`, so 9 999 of them are decoys and reading the whole capture is pointless.

The intended index is 1337. That packet's archive is AES encrypted and its password is `1337` as well; the decrypted `flag.txt` says, in plain words, `the password is glinomesieforever`.

Why this step: the index is the hint, and the nested archive is the trap; a script that iterates packets must verify the decrypted content instead of trusting an array position.

## 8. Layer 8: the hotel

`task8.zip` (opened by `glinomesieforever`) holds `desc.md` and a photo. The description asks, in Russian, in which hotel the author fell asleep, and specifies the password format: the hotel name in English, lowercase, no spaces.

The photo is taken from a capsule hotel bed and the window shows a building with the sign `EBIHARA` and a shop front. Akihabara plus capsule hotel narrows it to one well known chain, and `superhotelpremierakihabara` authenticates `task9.zip`. EXIF is stripped, so this is pure visual OSINT, no metadata shortcut.

## 9. Layer 9: obfuscated JavaScript and ROT13

`task9.zip` contains a 59 MB obfuscated `checker.js` and `flag.zip`. Reading the constant the obfuscator produces gives `junkjunkjunkjunkcode`, the password for `flag.zip`.

Inside, `flag.txt` is ROT13 and starts with `pgsphc{`, which is `ctfcup{` rotated. Decoding the whole line gives the accepted flag. It is masked here as `ctfcup{...}`.

Why this step: the prefix check is enough to recognise ROT13, and the challenge format is the confirmation.

## 10. Why it was hard

No layer uses exotic cryptography. The difficulty is knowing when to change tools:

- metadata instead of steganography for the PNG
- real jigsaw reconstruction instead of password guessing, with the archive as oracle
- constraint solving instead of running an alien architecture
- phonetic transcription instead of audio brute force
- packet indexing instead of reading 10 000 archives
- visual OSINT instead of EXIF
- ROT13 at the end, after eight layers of real work

The discipline that made it finishable: after every layer, verify the result against the next archive. A wrong guess cannot propagate.

One paragraph: the chain goes from a year question through a repaired PNG header, a reassembled jigsaw, a MIPS crackme, a spoken NATO password, Base64 in an image, packet 1337 of a ten thousand packet capture, an Akihabara hotel photo and an obfuscated JavaScript constant, ending in a ROT13 flag; each password is confirmed by the AES archive HMAC before the next layer is attempted.
