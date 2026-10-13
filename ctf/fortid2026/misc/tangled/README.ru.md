# Tangled - два чертежа Factorio, прячущие два QR-кода

- **Платформа:** FortiD CTF 2026
- **Категория:** Misc
- **Автор:** laragrr
- **Очки:** 436
- **Дата:** 2026-10-13
- **Флаг:** `FortID{...}` (замаскирован; подтверждён - введён на платформе и принят)
- **Артефакт:** `handout_file_1.txt`, `handout_file_2.txt` (экспорты чертежей Factorio)

## Что в задаче

> An abandoned factory hides a message within its tangled machinery. Untangle the construction and uncover the secret.
> «Заброшенная фабрика скрывает сообщение среди запутанных механизмов. Распутайте конструкцию и откройте секрет».

Два текстовых файла. Каждый содержит одну длинную строку, начинающуюся с `0eN...`, - на вид бессмысленный набор букв, цифр, плюсов и косых черт. Явного `FortID{...}` нет.

Файлы - это **экспорты чертежей Factorio**: `0` + base64( zlib( JSON ) ), где JSON описывает фабрику (машины, ленты, плитки, провода) координатами. Секрет спрятан **геометрически** - в типах и позициях объектов.

Из двух файлов восстанавливаются два QR-кода:

- плитки файла 1 (`landfill` против `foundation`) дают QR 29x29 (версия 3) с первой частью флага;
- XOR: объекты файла 1 и плитки файла 2 (обе маски нормализованы к сетке 25x25) дают QR 25x25 (версия 2) со второй частью;
- части собираются во флаг (с тонкостью про завершающее подчёркивание, см. `solve.md`).

Проверено: солвер воспроизводит оба QR-фрагмента из оригинальных файлов, и итоговый флаг был введён на платформе и принят.

Полный разбор - в [`solve.md`](solve.md).

## Структура папки

```text
misc/tangled/
├── README.md          # это файл: условие задачи
├── solve.md           # решение: декодирование, маски, XOR, QR, сборка
├── assets/
│   ├── handout_file_1.txt   # чертёж 1: объекты + плитки landfill/foundation
│   └── handout_file_2.txt   # чертёж 2: только landfill-маска
├── scripts/
│   └── solve.py       # воспроизводимый солвер: file1 file2 --out <dir>
└── evidence/
    ├── 00_overview.png      # обзор двух чертежей
    ├── 01_qr_first.png      # QR из типов плиток файла 1 (29x29)
    ├── 02_entities.png      # маска объектов файла 1 (25x25)
    ├── 03_second_tiles.png  # landfill-маска файла 2 (25x25)
    ├── 04_qr_xor.png        # QR из XOR двух масок (25x25)
    └── decoded_fragments.txt
```

## Воспроизведение

```bash
cd misc/tangled
python3 -m venv .venv && source .venv/bin/activate
pip install numpy opencv-python-headless pillow
python3 scripts/solve.py assets/handout_file_1.txt assets/handout_file_2.txt --out /tmp/tangled_out
```

Ожидаемый вывод (строка флага здесь замаскирована):

```text
blueprint 1: 290 entities; 841 tiles; 104 wires
blueprint 2: 0 entities; 330 tiles
first QR mask: (29, 29), black modules=429
entity mask: (25, 25), occupied modules=290
second tile mask: (25, 25), occupied modules=330
overlap: 144, XOR modules=332
first QR:  FortID{Wh3n_7h3_8e1ts_...}   (замаскирован)
second QR: _5p49hett1_..._   (замаскирован)
candidate flag: FortID{Wh3n_7h3_8e1ts_...}   (замаскирован)
```