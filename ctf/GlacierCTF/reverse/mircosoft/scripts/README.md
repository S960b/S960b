# mircosoft — fully reproducible scripts

[English technical write-up](../solve.md) · [Русский райтап](../solve.ru.md)

**All stages have now been reproduced from the original organizer archive.** The earlier missing `unpacked_pe.bin` is no longer a prerequisite: [`unpack.py`](unpack.py) regenerates it from `mircosoft.exe`.

## Simplest method

```sh
python3 scripts/reproduce.py 'mircosoft (1).tar.gz'
```

The one-shot runner uses a temporary work directory and standard Python only. It:

1. extracts the official tarball;
2. calls `unpack.py` to recover the inner PE from the loader;
3. checks `SHA256(unpacked_pe.bin)=9d5df8e2717f29b5ee8af7a0b2c423de754aac7676e65c4429bc74361f191d80`;
4. calls `extract_minidump.py` to extract seven regions;
5. invokes `solve.py` with `MIRCOSOFT_WORKDIR`, checks computed MAC equals the 16-byte license header, and prints the flag.

## Run the individual stages

```sh
tar -xzf mircosoft.tar.gz
mkdir -p ./work/mircosoft
cp mircosoft/license.bin ./work/mircosoft/
python3 scripts/unpack.py mircosoft/mircosoft.exe -o ./work/unpacked_pe.bin
python3 scripts/extract_minidump.py mircosoft/admin_machine.dmp --out-dir ./work
MIRCOSOFT_WORKDIR="$(pwd)/work" python3 scripts/solve.py
```

The historical `solve.py` retains its complete original XTEA/MAC/key-derivation logic, with only an optional environment-based workdir added; absent that environment variable, its prior path `/mnt/data/mircosoft_work` remains the default.

## Source provenance

The `unpack.py` code and a detailed bilingual `mircosoft_WRITEUP_RU_EN.md` were retrieved from the original solved conversation's Library materials. The extracted inner PE has exactly 18,432 bytes and the expected hash. The original final solver's printed MAC exactly matched `license.bin`, and the plaintext contained the accepted flag. These results were independently rerun with the original current-conversation tarball.

**Do not mistake** `unpacked_pe.bin` for organizer-provided data. It is computed by `unpack.py`. The 16-byte DRM keys reside in the resulting `.data` segment at file offset `0x3e00`.
