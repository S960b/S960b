# suntrail — writeup (forensics, a flag buried in a keyboard layout)

## What the task was

One artefact: `suntrail.klc`, 419 bytes of plain ASCII. Category: forensics. The flag is somewhere inside it, and there is no hint beyond the name — "suntrail" — which turns out to be literal: there is a *trail* through the file.

The extension is the whole story: `.klc` is a **Microsoft Keyboard Layout Creator** source file. It is not a document with hidden streams — it is a keyboard layout definition, i.e. a table that says "when this key is pressed in this shift state, output this Unicode character".

---

## Step 1 — look at the file (it is tiny)

```bash
file suntrail.klc          # ASCII text
wc -lc suntrail.klc        # 33 lines, 419 bytes
cat suntrail.klc
```

With 33 lines there is nothing to grep for — read the whole thing:

```text
KBD	kbdusx	"US"

SHIFTSTATE

0
1
2

LAYOUT

10	Q	0	2198	0073	-1
11	W	0	2192	0077	-1
12	E	0	2198	0065	-1
13	R	0	2192	0073	-1
14	T	0	2198	0075	-1

1e	A	0	2198	0075	-1
1f	S	0	2196	0071	-1
20	D	0	2198	0072	-1
21	F	0	2196	005f	-1
22	G	0	2198	0063	-1
23	H	0	25a0	007d	-1

2c	Z	0	2192	006e	-1
2d	X	0	2196	007b	-1
2e	C	0	2192	0074	-1
2f	V	0	2196	0079	-1
30	B	0	2192	006b	-1
31	N	0	2196	0073	-1

39	SPACE	0	0020	0020	-1

ENDKBD
```

Reading the format:

- `KBD kbdusx "US"` — internal name and the description shown in Windows;
- `SHIFTSTATE` lists the states the table has columns for: here `0` (normal), `1` (Shift), `2` (AltGr / Ctrl+Alt);
- every `LAYOUT` line is `scan code`, `virtual key`, `caps`, then one Unicode value per shift state in the same order, `-1` meaning "nothing".

So `10 Q 0 2198 0073 -1` reads: scan code `0x10`, key `Q`, normal state outputs `U+2198`, Shift outputs `U+0073` (`s`), AltGr gives nothing.

That is already suspicious: in a **real** layout the normal column holds the character the key prints (`q` for Q). Here the normal column holds something else entirely.

## Step 2 — the tools that reveal the trick

The normal column holds Unicode arrows, not letters. The values are hexadecimal, so any Unicode reference turns them into meaning. Pick whichever you have:

```bash
uni hex 0x2198                      # unicode-utils (Debian: package unicode)
python3 -c "import unicodedata; print(unicodedata.name(chr(0x2198)))"
gucharmap                           # GUI character map
```

Result:

```text
2192  ->   RIGHTWARDS ARROW
2196  ↖   NORTH WEST ARROW     (movement: up-left)
2198  ↘   SOUTH EAST ARROW     (movement: down-right)
25a0  ■   BLACK SQUARE         (stop)
```

The Unicode *names* are the instructions. The file is a map with a route drawn on it:

- `→` = next key is one step **right**,
- `↘` = one step **down-right** (remember the rows of a real keyboard are offset half a key, which is why "down-right" from `Q` is `A` and from `T` it is `G`),
- `↖` = one step **up-left**,
- `■` = **stop**.

The second column (shift state 1) holds the letters. So: start somewhere, follow the arrows, collect the shifted characters.

## Step 3 — walk the trail

Start where the file starts: the first key in the `LAYOUT` table is `Q`. Follow the arrows:

| scan | key | mark | shifted | next |
|------|-----|------|---------|------|
| 10 | Q | ↘ down-right | `s` | A |
| 1e | A | ↘ down-right | `u` | Z |
| 2c | Z | → right | `n` | X |
| 2d | X | ↖ up-left | `{` | S |
| 1f | S | ↖ up-left | `q` | W |
| 11 | W | → right | `w` | E |
| 12 | E | ↘ down-right | `e` | D |
| 20 | D | ↘ down-right | `r` | C |
| 2e | C | → right | `t` | V |
| 2f | V | ↖ up-left | `y` | F |
| 21 | F | ↖ up-left | `_` | R |
| 13 | R | → right | `s` | T |
| 14 | T | ↘ down-right | `u` | G |
| 22 | G | ↘ down-right | `c` | B |
| 30 | B | → right | `k` | N |
| 31 | N | ↖ up-left | `s` | H |
| 23 | H | ■ stop | `}` | end |

Route: `Q → A → Z → X → S → W → E → D → C → V → F → R → T → G → B → N → H`

Two sanity checks make this reading trustworthy instead of a guess:

1. the route visits **all 17 keys** in the file, never twice;
2. it ends exactly on the only key marked `■` (stop).

Collected letters (masked here — the published `.klc` has three of them replaced by dots): `sun{qwer...sucks}`.

`scripts/parse_klc.py` does the same mechanically:

```bash
python3 scripts/parse_klc.py assets/suntrail.klc
#   ... route: Q -> A -> Z -> X -> S -> W -> E -> D -> C -> V -> F -> R -> T -> G -> B -> N -> H
#   ... flag : sun{qwer...sucks}      <- in the published copy three letters are dots
#   ... checks: 17 keys in file, 17 visited, route covers all keys: True
```

Flag (masked): `sun{qwer...sucks}`

---

## What I tried first (and why each failed)

1. **Searching for the flag as text.** `grep -a 'sun{' suntrail.klc` finds nothing: the letters exist only as separate `0020`-style code points scattered across the table, in route order — never as a contiguous string.

2. **`strings` / `xxd`.** The file is plain text, so this adds nothing. The "hiding" is not in the bytes, it is in the *meaning* of the numbers.

3. **Reading the first column as characters.** The natural assumption is "first column = what the key prints". That leads nowhere, because those columns hold arrows, not letters — the giveaway is that the values are `2192`-ish instead of `0061`-ish. Converting them to Unicode names is what unlocks the puzzle.

4. **Guessing the route by reading the table in file order.** The letters in file order are `s w e s u u q r _ c } n { t y k s` — noise. The order is defined by the arrows, not by the file.

5. **Walking the arrows with naive coordinates.** My first script treated the key grid as a plain rectangle, so "down-right" from `Q` landed on `D` and the trail broke after eight keys. Real keyboards offset each row by half a key; with that offset the walk is clean (`Q→A→Z→X…`). The "route covers all keys, and stops on ■" check is what exposes such a mistake.

6. **Loading the layout into the OS to type it.** It works (`klc2xkb` → `xkbcomp` → `setxkbmap`, or MSKLC on Windows), but it is theatre: 17 lookups in a table give the same answer with none of the risk of setting a weird keyboard layout as your active one.

---

## One-paragraph version (for interviews)

The artefact is a Microsoft Keyboard Layout Creator file, i.e. a table: for each key and shift state, which Unicode character is produced. Its `LAYOUT` table is 17 keys; the unshifted column contains Unicode arrows (`U+2192` right, `U+2196` up-left, `U+2198` down-right, `U+25A0` stop) and the shifted column contains letters. Starting from the first key and following the arrows across the physical key grid — accounting for the half-key offset between keyboard rows — visits every key exactly once and ends on the only "stop" key; collecting the shifted characters in that order gives the flag. No Office, no MSKLC, no emulation: a text file, a Unicode table and a loop.

---

## Lessons learned

1. **Read the small artefact completely.** 419 bytes / 33 lines — `cat` beats any tooling decision.
2. **Recognise the file format from its header.** `KBD` + `SHIFTSTATE` + `LAYOUT` is a `.klc`; that immediately tells you the columns mean "key × shift state → character".
3. **When numbers look like Unicode code points, decode the names.** `uni hex 0x2198` turned a column of hex into a set of movement instructions.
4. **Data can be hidden as meaning, not as obfuscation.** Nothing here is encrypted or compressed; the information is in how the file is structured.
5. **Validate a reading with a structural invariant** (visits all keys once, ends on the stop marker). It turns a plausible decode into a certain one — and caught my own wrong coordinate model.
6. **A trail puzzle needs the right geometry.** Keyboard rows are offset; "diagonal" transitions are what make the route connect.
