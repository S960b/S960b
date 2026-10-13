# M4st3r H4ck3r (retrieve) - разбор (флаг в пути аномального PNG)

## Знакомство с задачей

Описание задачи - одна строка:

> Heard you're a M4st3r H4ck3r, can you retrieve the flag from this archive?
> **trie** - выделено чёрным цветом

Слово «trie» выделено специально - это намеренная подсказка. Поэтому сначала я посмотрел, что в архиве и к чему может относиться «trie». (Для справки: вне этой задачи «trie» - это название структуры данных «префиксное дерево», см., например, https://en.wikipedia.org/wiki/Trie, - и именно такую форму архив и принимает.)

## Первое знакомство с архивом

```bash
$ unzip -l retrieve.zip | head
...
    48857  2026-08-07 15:13   retrieve/81/81/68/86/95/90/104/67/98/84/104/103/40/58/90/103/104/66/78/107/71/80/95/70/67/112/110/117/120/78/76/95/118/120/66/73/40/113/70/66/eow.png
    48857  2026-08-07 15:13   retrieve/81/114/93/105/43/58/66/106/122/112/45/43/75/115/101/51/118/107/45/53/117/65/89/79/87/58/116/72/99/116/121/72/46/76/70/111/65/90/77/46/97/110/100/76/eow.png
    ...
```

В архиве 7000 файлов. Каждый путь - это глубокое дерево каталогов, где **каждое имя директории - это ASCII-код одного символа**, а в конце лежит `eow.png` (48857 байт).

То есть полные пути - это **строки, закодированные посимвольно**, и все они собраны в одно гигантское префиксное дерево (trie). Каждая строка - кандидат во флаг.

## Декодирование путей

```python
import zipfile
z = zipfile.ZipFile('/home/kali/Downloads/retrieve.zip')

def decode(n):
    return ''.join(chr(int(x)) for x in n.split('/')[1:-1])

names = z.namelist()
for n in names:
    print(decode(n))
```

Верхний уровень (`retrieve/`) - это не код, а фиксированный корневой каталог архива, поэтому его надо пропускать. В финальном солвере это сделано не по индексу, а по признаку «компонент - число»: так код не ломается, если платформа переименует корневую папку.

Декодированные строки выглядят так:

```text
FortID{waV3form_relay_frame_kernel_H34der...}
FortID{proxy_neon_term1nal_72082]
FortID{plot_twist_this_one_is_also_fake}
FortID{please_ignore_the_suspiciously_flag_shaped_string}
```

Большинство - явные приманки (фейковые флаги), часть вообще не закрыта `}`. Но все они построены по одному шаблону-словарю (retro-тематика: proxy, payload, sector, xmodem, dialup, cartridge...).

## Что внутри PNG

Проверил содержимое PNG:

- Каждый PNG - это мем про хакера («HACK THE PLANET», «#1 HACK3R»), все одного размера **290x320**.
- Внутри каждого PNG есть tEXt-chunk `Comment` с hex-строкой - там спрятаны какие-то 118 байт.
- Все 7000 файлов попарно уникальны (7000 уникальных MD5).

Начал было копать в сторону стеганографии в пикселях - но тут вылезло кое-что важное.

## Находка: «не такой» файл

При массовом подсчёте размеров картинок обнаружился **один файл с размерами 288x320**, а все остальные - 290x320:

```text
retrieve/70/111/114/116/73/68/123/68/48/95/111/114/95/68/48/95/78/48/55/95/84/104/51/114/51/95/49/53/95/110/48/95/84/114/105/51/125/eow.png
```

Это единственное отклонение в 7000 файлах - явная аномалия. Декодируем путь:

```python
>>> ''.join(chr(int(x)) for x in path.split('/')[1:-1])
'FortID{D0_or_D0_...}'   (замаскирован)
```

## Разбор флага

`D0_or_D0_...` - это отсылка к Йоде: *"Do or do not, there is no try"*. Здесь «try» заменено на структуру данных **trie** - ровно та подсказка, которую выделили чёрным в условии. Намёк сработал буквально: «в этом архиве нет trie-дерева» - это ложный след, а настоящий флаг просто лежит в пути аномального файла. Флаг сдаётся и проходит.

## Полный солвер

`scripts/solve.py` читает из каждого PNG только первые 33 байта (сигнатура + IHDR), поэтому проход по 7000 файлам занимает доли секунды:

```python
#!/usr/bin/env python3
"""retrieve / "M4st3r H4ck3r" (FortID) - the flag is the path of the odd PNG.

The archive is a trie: every directory name is an ASCII code of one character, every
leaf is eow.png. The decoded paths are mostly decoy flags. 6999 images are 290x320 and
exactly one is 288x320 - its path is the real flag.

Usage:
    python3 solve.py [retrieve.zip]          # default: ~/Downloads/retrieve.zip
    python3 solve.py --dir <extracted-dir>
"""

import argparse
import collections
import os
import struct
import sys
import zipfile

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
DEFAULT_ZIP = os.path.expanduser("~/Downloads/retrieve.zip")


def decode_path(name):
    """retrieve/70/111/.../eow.png - the characters those ASCII codes spell.

    The archive has a fixed top-level dir (``retrieve/``) that is not a code, so
    leading non-numeric components are skipped; after the first code, anything
    non-numeric means the path is not built the way we expect.
    """
    parts = [p for p in name.split("/") if p][:-1]
    codes = []
    for part in parts:
        if part.isdigit():
            codes.append(int(part))
        elif codes:
            raise ValueError(f"unexpected non-numeric path component: {part!r}")
    return "".join(chr(c) for c in codes)


def png_size_from_bytes(head):
    if head[:8] != PNG_MAGIC or len(head) < 24:
        return None
    width, height = struct.unpack(">II", head[16:24])
    return width, height


def scan_zip(path):
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            with zf.open(info) as fh:
                size = png_size_from_bytes(fh.read(33))
            yield info.filename, size


def scan_dir(path):
    for root, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(root, name)
            with open(full, "rb") as fh:
                size = png_size_from_bytes(fh.read(33))
            yield os.path.relpath(full, path), size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archive", nargs="?", default=DEFAULT_ZIP, help="retrieve.zip")
    ap.add_argument("--dir", help="scan an already extracted tree instead of the zip")
    args = ap.parse_args()

    if args.dir:
        source, items = args.dir, scan_dir(args.dir)
    else:
        if not os.path.exists(args.archive):
            raise SystemExit(f"missing archive: {args.archive}")
        source, items = args.archive, scan_zip(args.archive)

    sizes = collections.Counter()
    by_size = collections.defaultdict(list)
    for name, size in items:
        sizes[size] += 1
        by_size[size].append(name)

    print(f"[*] scanned {sum(sizes.values())} files from {source}")
    for size, count in sizes.most_common():
        print(f"    {size[0]}x{size[1]}: {count} files")

    anomalies = [size for size, count in sizes.items() if count == 1]
    if not anomalies:
        print("[!] no unique-size file found")
        return 1

    for size in anomalies:
        name = by_size[size][0]
        decoded = decode_path(name)
        print(f"\n[+] anomalous file {size[0]}x{size[1]}: {name}")
        print(f"    decoded: {decoded}")
        if decoded.startswith("FortID{") and decoded.endswith("}"):
            print(f"\nFLAG: {decoded}")
            return 0

    print("[!] the anomalous path does not look like a flag")
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

Прогон на живом архиве (проверено):

```bash
$ python3 scripts/solve.py ~/Downloads/retrieve.zip
[*] scanned 7000 files from /home/kali/Downloads/retrieve.zip
    290x320: 6999 files
    288x320: 1 files

[+] anomalous file 288x320: retrieve/70/111/114/116/73/68/123/.../eow.png
    decoded: FortID{D0_or_D0_...}   (замаскирован)

FLAG: FortID{D0_or_D0_...}   (замаскирован)
```

## Тупики (что я рассматривал сначала)

1. **Стеганография в пикселях PNG.** Мемы выглядят одинаково, но файлы все уникальны (7000 уникальных MD5), и в каждом PNG есть tEXt-chunk с hex-строкой. Копать в пиксели было заманчиво - но аномалия размера это более быстрый и точный сигнал, и она нашлась первой.
2. **Читать все 7000 декодированных путей глазами.** Это приманки из общего retro-словаря; по чтению настоящий флаг не отличить.

## Версия в один абзац

Архив - это trie: 7000 файлов `eow.png`, пути которых из ASCII-кодов образуют строки - в основном фейковые флаги. Настоящий спрятан как аномалия: 6999 картинок размера 290x320 и ровно одна 288x320. Солвер читает только заголовок PNG (33 байта на файл), считает размеры, находит единственный 288x320 файл, декодирует его путь и печатает флаг - `FortID{D0_or_D0_...}` (отсылка к Йоде «Do or do not, there is no try», где try заменено на слово-подсказку trie).

## Уроки

1. Выделенное слово в описании - это подсказка, а не украшение: «trie» указало на древесную структуру путей раньше всего остального.
2. Когда тысячи файлов «одинаковые», самый надёжный сигнал - измеримое отличие (размер), а не рассматривание содержимого: сначала подсчёт, потом взгляд.
3. Чтение только заголовка файла (33 байта) вместо всего архива из 7000 файлов делает скан мгновенным.
4. Приманки - часть дизайна: файл с уникальным размером и есть аномалия, а его путь - флаг.

## Файлы

- `scripts/solve.py` - солвер: `python3 solve.py [retrieve.zip]` или `--dir <распакованное дерево>`