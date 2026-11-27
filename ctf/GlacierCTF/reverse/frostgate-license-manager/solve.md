# Frostgate License Manager — virtualized Windows license check (GlacierCTF 2026)

[Русская версия](solve.ru.md) · [C solver](scripts/solve_virt.c) · [Verification](evidence/README.md)

**Author:** xer0 · **Category:** rev · **Points:** 50 · **Status:** solved, independently reproduced on the original organizer PE.

# English version

## Overview

The challenge shipped a 64-bit Windows PE, `virt.exe`, posing as **Frostgate License Manager v3.1**. It asked for a license key and stated that a correct key was the flag. The binary contained a custom executable `.frost0` section and two validation paths. The initial apparent key was a deliberate decoy; the actual solution required running the virtualized checker.

## Recon and decoy

The PE was linked at **ImageBase `0x100000000`**. The relevant sections were `.text`, `.data`, and `.frost0` (RVA `0x29000`). A check at **RVA `0x125c0`** accepted 39-byte inputs and compared the bytes against data decoded with:

```python
candidate[i] = decoy_table[i] ^ ((0x2f * i + 0xa7) & 0xff)
```

That produced:

```text
wrong{f1n4l1z3r_d3c0y_n0t_th3_r34l_0n3}
```

Since it is a 39-character `wrong{...}` string and not the proper flag format, we did not stop there.

## Recovering the real validation loop

The real checker at **RVA `0x12490`** expects **40 bytes**. It initializes a 32-bit state as `length ^ 0xDEADBEEF`, and for every index performs two virtualized calls:

```text
vm_check(character, state, index) at RVA 0x12340
vm_next (character, state, index) at RVA 0x12410
```

The first return value is compared with the expected byte in a **40-byte lookup table at RVA `0x19860`**. The second return value becomes the state for the next position. A mismatch is accumulated with bitwise OR, so every position must match.

The `.frost0` dispatcher at **RVA `0x29000`** contains self-modifying x86-64 instructions. Static inspection found **413 RIP-relative patch sites**: 339 four-byte, 43 two-byte, and 31 one-byte writes. Those sites can be applied to an extracted copy of `.frost0` for inspection, but reconstructing every VM opcode was unnecessary.

## Exploit-free solution: use the VM itself

We mapped the PE sections into anonymous executable+writable memory at the original ImageBase and invoked the original two functions with GCC's **Microsoft x64 ABI** (`ms_abi`). This avoided running the Windows program's entry point or implementing the full VM interpreter.

For each of the 40 positions, we tried all printable ASCII characters `0x20..0x7e`, retaining the unique one for which `vm_check(candidate, state, index) == expected[index]`. Only after choosing the correct byte did we advance `state = vm_next(...)`.

Because the state depends only on the already recovered prefix, the search needs **3,800 candidate checks**, not a brute-force search over 40-character strings. All 40 positions had exactly one printable candidate.

The first character was `g`, with initial state `0xdeadbec7`; the next state was `0x6d7267f3`. The final state after `}` was `0x14792a50`. The fully recovered flag is:

```text
gctf{...}
```

The next section contains everything needed to reproduce the result from your own unmodified challenge executable on **Linux x86-64**. A full executable hash and exact commands are included. No copy of the original binary is provided.

---



# Воспроизведение / Reproduction

## Требования / Prerequisites

- Linux on **x86-64** (или Linux VM/WSL2 с доступным отображением памяти по `0x100000000`).
- `gcc` и стандартные заголовки Linux; `python3` — только для дополнительных статических проверок.
- Своя копия исходного `virt.tar.gz` — **не приложена к этому райтапу**.

Начальные команды / Initial commands:

```bash
mkdir -p virt-repro && cd virt-repro
# Скопируйте сюда свой virt.tar.gz / Place your own archive here.
tar -xzf virt.tar.gz
sha256sum virt/virt.exe
# expected:
# ec5620b986ee7102054e676998dedcea7e25f101b0def8d7366f83010bf16ce2
```

## A. Проверка ложной ветки / Verify the decoy

Команда читает таблицу по RVA из PE и расшифровывает её без сторонних библиотек / The command extracts and decodes the decoy table from the PE without external libraries:

```bash
python3 - <<'PY'
from pathlib import Path
import struct
b = Path('virt/virt.exe').read_bytes()
pe = struct.unpack_from('<I', b, 0x3c)[0]
s = pe + 24 + struct.unpack_from('<H', b, pe + 20)[0]
for i in range(struct.unpack_from('<H', b, pe + 6)[0]):
    h = s + i * 40
    name = b[h:h+8].rstrip(b'\0')
    if name == b'.data':
        rva = struct.unpack_from('<I', b, h + 12)[0]
        file_offset = struct.unpack_from('<I', b, h + 20)[0]
        start = file_offset + (0x19890 - rva)
        encoded = b[start:start + 39]
        decoded = bytes(x ^ ((i * 0x2f + 0xa7) & 255)
                        for i, x in enumerate(encoded))
        print(decoded.decode('ascii'))
        break
PY
# wrong{f1n4l1z3r_d3c0y_n0t_th3_r34l_0n3}
```

## B. Самоперезапись `.frost0` / Inspect `.frost0` self-modification (optional)

Следующий вспомогательный скрипт можно сохранить как `deob_virt.py`. Он **не изменяет исходный EXE**, а создаёт `frost_patched.bin` для изучения в дизассемблере. Скрипт не требуется для работы решателя.

The optional static deobfuscation program is preserved as [`scripts/deob_virt.py`](scripts/deob_virt.py).

Запуск / Run:

```bash
python3 deob_virt.py virt/virt.exe
# section RVA: 0x29000
# counts by patch width: {1: 31, 2: 43, 4: 339}
# total patches: 413
# saved: frost_patched.bin (inspection only)
```

Это копия изменённых байтов для офлайн-анализа, не готовая самостоятельная PE-программа. Результат можно отдельно дизассемблировать: / The patch output is an inspection copy, not a runnable PE:

```bash
objdump -D -b binary -m i386:x86-64 -Mintel \
  --adjust-vma=0x100029000 frost_patched.bin | less
```

## C. Полный решатель / Complete native solver (`solve_virt.c`)

Сохраните приведённый ниже исходный код как `solve_virt.c`. Он сам читает секции PE, таблицу ожидаемых значений и вызывает настоящую VM в памяти. **Флаг в программе не захардкожен.** / Save the following as `solve_virt.c`. It reads the original PE, maps its sections, extracts the real target table and invokes the VM in memory. **The flag is not hardcoded.**

The complete original, flag-independent C solver is preserved as [`scripts/solve_virt.c`](scripts/solve_virt.c).

## D. Сборка и результат / Build and verify

```bash
gcc -O2 -Wall -Wextra -o solve_virt solve_virt.c
./solve_virt virt/virt.exe
```

Для короткой проверки вывода / For a short output check:

```bash
./solve_virt virt/virt.exe | tail -3
```

Ожидаемый вывод / Expected output:

```text
39  }  state=14792a50
FLAG: gctf{...}
final state: 14792a50
```

При успехе решатель завершается с кодом `0`. Если на какой-либо позиции число подходящих ASCII-символов не равно единице, он останавливается и выводит состояние и ожидаемый байт. Если фиксированная база `0x100000000` недоступна, `mmap` сообщит об ошибке; используйте изолированную x86-64 Linux-среду с доступной памятью по этому адресу. На ARM или Windows этот Linux-харнесс напрямую не рассчитан.

Воспроизведение проверено на оригинальном файле с указанным SHA-256: все 40 позиций восстановлены единственным образом, начальное состояние `0xdeadbec7`, конечное — `0x14792a50`. В результате не использовались заранее известные символы флага, кроме проверки формата `gctf{...}` **после** полного восстановления.

---

## Итог / Takeaway

В задаче сочетались **ложная проверка**, **виртуализация контрольных функций** и **самомодифицирующийся диспетчер**. Главное решение — отделить внешний алгоритм сверки от скрытой реализации операций, затем использовать доступные бинарные функции как точный оракул. Это позволило восстановить все 40 символов без полной ручной деобфускации VM.

The key insight was to separate the visible validation loop from the virtualized primitives. Once the comparison table and state dependency were known, **the original VM could serve as its own oracle**. A short native harness recovered all 40 characters exactly, without reproducing the entire VM interpreter.