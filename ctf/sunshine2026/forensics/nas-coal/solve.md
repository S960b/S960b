# NAS coal - writeup (forensics, a macro inside a PowerPoint deck)

## What the task was

We get one file: `gem_collection.pptm`, a 2.2 MB macro-enabled PowerPoint presentation ("My gem collection"). Five slides, some pictures, a few jokes. The task name is the hint - **NAS coal** - and the category is forensics, so the flag is somewhere in the file, not on the screen.

There is no Windows and no Office in the environment, and we do not need either: a `.pptm` is just a ZIP archive with XML and binary parts, and the malicious part of such files is always a VBA project under `ppt/vbaProject.bin`.

---

## Step 1 - look at the file the cheap way

```bash
file gem_collection.pptm
# Microsoft PowerPoint 2007+

unzip -l gem_collection.pptm | grep -iE 'vba|macro'
#   13312  2026-09-19  ppt/vbaProject.bin
```

That is the whole recon: `.pptm` = OOXML ZIP container, and it ships a VBA project. Docs (`docm`) and sheets (`xlsm`) work exactly the same way.

Reading the slide XML is worth a minute - the author left a deliberate hint (slide 5: `marge? > mfw  olevba  oneshot  chall`, slide 1: `My gem collection (NAS thoughever)`):

```bash
for f in $(unzip -Z1 gem_collection.pptm 'ppt/slides/slide*.xml'); do
  unzip -p gem_collection.pptm "$f" | grep -o '<a:t>[^<]*</a:t>' | sed 's/<[^>]*>//g'
done
```

Slides carry text and images only - the flag is not in the XML.

## Step 2 - the tools that make this a five-minute job

The hint says it outright: **olevba**. `oletools` is the toolkit for OLE/OOXML office documents (no Office, no macros executed):

```bash
pip install oletools            # or: pipx install oletools

oleid gem_collection.pptm       # quick triage
#  VBA Macros   Yes   Medium   This file contains VBA macros...
#  XLM Macros   No
#  External Relationships 0

mraptor gem_collection.pptm     # "macro malware?" verdict
#  Result    |Flags|Type|File
#  Macro OK  |---  |OpX:| gem_collection.pptm
#  Flags: A=AutoExec, W=Write, X=Execute
```

Note what `mraptor` says here: **Macro OK**, no flags - the sub is not `Auto_Open`, and it never writes to disk, so the "macro malware" heuristic stays quiet. Automatic verdicts do not help in this task; you have to dump the code:

```bash
olevba gem_collection.pptm      # the actual dump: source code + keyword table
```

`olevba` prints the decompiled module `MediaCache.bas` and flags the interesting keywords:

```text
VBA MACRO MediaCache.bas
Option Explicit

Public Sub RefreshCache()
    Dim encoded As String
    Dim commandLine As String
    encoded = "JABjAGEAbQBwAGEAaQBnAG4AIAA9ACAA...  (584 chars of Base64)  ..."
    commandLine = "powershell.exe -NoProfile -EncodedCommand " & encoded
    Debug.Print commandLine
End Sub

| Suspicious | powershell      | May run PowerShell commands |
| Suspicious | EncodedCommand  | May run PowerShell commands |
| Suspicious | Base64 Strings  | Base64-encoded strings ...  |
```

That is the whole challenge in one screen: a Base64 blob, and a command line that feeds it to `powershell.exe -EncodedCommand`.

## Step 3 - decode the blob

PowerShell's `-EncodedCommand` takes Base64 of a **UTF-16LE** string, so plain `base64 -d` gives you interleaved NUL bytes. Decode with the right codec:

```bash
B64=$(grep -o 'encoded = "[^"]*"' assets/macro_MediaCache.bas | cut -d'"' -f2)
echo "$B64" | base64 -d | iconv -f UTF-16LE -t UTF-8
```

Same thing in PowerShell or Python, if you prefer:

```powershell
[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($b64))
```
```python
base64.b64decode(b64).decode('utf-16-le')
```

`scripts/decode_macro.py` does exactly this (and can pull the blob straight out of a `.pptm` via `olevba` if you have the original):

```bash
python3 scripts/decode_macro.py assets/macro_MediaCache.bas
```

Result (flag masked here, as in the published copy of the artefact):

```powershell
$campaign = 'sun{yup_...._gem}'
$source = 'https://gem-cache.example.invalid/coal.bin'
$destination = 'coal.bin'
[pscustomobject]@{
    Operation='download'
    Campaign=$campaign
    Source=$source
    Destination=$destination
}
```

The `$campaign` variable is the flag. Masked: `sun{yup_...._gem}`.

Worth noting what the macro *would* do if it ran: it only builds a string and `Debug.Print`s it (the download target is `example.invalid`), so the challenge is about reading the file, not about emulating malware.

---

## What I tried first (and why each failed)

1. **Looking at the slides.** Text and pictures only; the hints are there, the flag is not. Slide 5 literally names `olevba` - read the slides first, it costs one command.

2. **`strings` on the file.** Useless in two ways: the deck is ZIP-compressed, and inside the ZIP the VBA module is stored in the MS-OVBA *compressed* form. `unzip -p gem_collection.pptm ppt/vbaProject.bin | strings` finds the ASCII fragments `powershell.exe -NoProfile -EncodedCommand` and `encoded` (a useful nudge), but **not the Base64 blob** - it is inside the compressed module stream, so only a VBA-aware parser (olevba) can get it out.

3. **Searching the raw bytes for the flag.** The flag string never appears in plaintext anywhere: inside the macro it exists only as part of that Base64 blob, and the blob itself is compressed again by the VBA project format. Byte-grepping the `.pptm`/`vbaProject.bin` for `sun{`, `campaign` or the Base64 fragment returns nothing.

4. **`olevba --decode`.** It does try to decode Base64 strings, but for a UTF-16LE PowerShell payload it prints only a truncated, NUL-riddled fragment. Take the string and decode it yourself - two commands, deterministic result.

5. **Opening the file in LibreOffice / PowerPoint to "run" the macro.** Not needed and not desirable: the macro is not the payload, the *text inside it* is the flag. Static tools give the answer without ever executing anything.

So the real lesson of this challenge: a macro-enabled Office file is a ZIP + an OLE VBA project, and `oleid` → `olevba` is the standard two-command path. Everything else is a detour.

---

## One-paragraph version (for interviews)

The artefact is a macro-enabled PowerPoint deck, which is a ZIP (OOXML) with a VBA project under `ppt/vbaProject.bin`. `oleid` confirms VBA macros, `olevba` dumps the module and shows the script: a long Base64 string passed to `powershell.exe -NoProfile -EncodedCommand`. Because `-EncodedCommand` expects Base64 of UTF-16LE, decoding the blob with the correct codec yields a short PowerShell snippet whose `$campaign` variable is the flag. `strings` and raw byte searches fail here: the deck is compressed and the VBA module is additionally stored in the compressed MS-OVBA form, so a VBA-aware parser is required; no Office and no macro execution is involved.

---

## Lessons learned

1. **Macro-enabled Office files are ZIP + OLE.** `unzip -l` first: `ppt/vbaProject.bin`, `word/vbaProject.bin`, `xl/vbaProject.bin` - that is where the code lives.
2. **`oleid` → `olevba` → `mraptor` is the standard path** and needs no Office and no privileges. `olevba` decompiles the module and highlights the suspicious keywords for you.
3. **PowerShell `-EncodedCommand` = Base64 of UTF-16LE.** Decoding with the wrong codec is the classic mistake; `iconv -f UTF-16LE` (or `.decode('utf-16-le')`) fixes it.
4. **`strings` is not enough** when the container compresses its parts (ZIP inside, MS-OVBA compression inside that). It gives hints, not payloads.
5. **Read the slides.** The author left the tool name (`olevba`) as plain text in the deck.
6. **Do not execute the macro.** For forensics, static extraction answers the question; running Office payloads is a risk with no upside.
