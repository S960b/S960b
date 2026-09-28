# suntrail - a flag hidden in a keyboard layout (.klc)

- **Platform:** SunshineCTF 2026
- **Category:** Forensics
- **Difficulty:** 293 points
- **Date:** 2026
- **Flag:** `sun{qwer...sucks}` (masked)
- **Link:** `<challenge-url>` (challenge instance, closed after the event)
- **Artefact:** `suntrail.klc` (419 bytes, plain text)

The artefact is a Microsoft Keyboard Layout Creator file: a keyboard layout definition. It does not contain the flag anywhere as text. Instead, the unshifted column of its `LAYOUT` table holds **Unicode arrows** instead of characters, and the shifted column holds letters:

```text
10   Q   0   2198(↘)  0073(s)   -1
1e   A   0   2198(↘)  0075(u)   -1
2c   Z   0   2192(→)  006e(n)   -1
...
23   H   0   25a0(■)  007d(})   -1
```

Starting on the first key in the file and following the arrows across the physical key grid (`→` right, `↘` down-right, `↖` up-left, `■` stop), the `shifted` column of the visited keys spells the flag. The route visits all 17 keys in the file exactly once, which is how you know the reading is right.

Files:

- `solve.md` - the full story, written for beginners, plus the tools that make it a five-minute job
- `scripts/parse_klc.py` - parses the `.klc` table, walks the trail and prints the flag
- `assets/suntrail.klc` - the artefact; in this published copy three letters (`t`, `y`, `_`) are replaced by dots so no live flag ships with the repo

No Windows and no MSKLC installation is needed: the file is text, and the only "tool" for the hidden part is a Unicode table.
