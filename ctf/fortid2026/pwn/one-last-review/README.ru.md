# One Last Review - use-after-free в heap-меню «оставь отзыв»

- **Платформа:** FortiD CTF 2026
- **Категория:** Pwn (heap, baby-pwn)
- **Автор:** (не зафиксирован в рабочей папке)
- **Очки / Решили:** (не зафиксированы в рабочей папке)
- **Дата:** 2026-10-13
- **Remote:** `ncat --ssl <host>:1337` (TLS; инстанс выдаёт платформа)
- **Флаг:** `FortID{...}` (замаскирован)
- **Артефакт:** `handout(2).zip` - `chall.c` + `chall` (ELF64, не PIE, not stripped)

## Что в задаче

Консольное меню «оставь отзыв» на 6 пунктов:

```text
1. Submit a review
2. Mark review as favourite
3. Delete review
4. Write a draft
5. Preview favourite review
6. Leave
```

`win()` в бинаре есть (и не выпилена компилятором), она открывает `flag.txt` и печатает его, но из меню никак не вызывается - значит, надо перехватить косвенный вызов.

Структура отзыва - ровно 40 байт: `char text[32]` + указатель на функцию `display`. `delete_review()` освобождает чанк и обнуляет `review`, но **не трогает `favourite`** - висячий указатель (UAF). `write_draft()` выделяет чанк того же размера (`draft_t.contents[sizeof(review_t)]`) и заполняет его нашими 40 байтами - то есть переиспользует освобождённый чанк. `preview_favourite()` вызывает указатель `display` избранного отзыва - подменяем его на `win` и получаем флаг.

Полный разбор - в [`solve.md`](solve.md).

## Структура папки

```text
pwn/one-last-review/
├── README.md          # это файл: условие задачи
├── solve.md           # решение: разбор, эксплойт, флаг
├── assets/
│   ├── chall.c        # исходник задачи
│   └── chall          # ELF64, non-PIE (win = 0x40135f, not stripped)
└── scripts/
    └── solve.py       # эксплойт: <host> [port] или --local
```

## Воспроизведение

```bash
python3 scripts/solve.py --local                       # локальная проверка на ./assets/chall
python3 scripts/solve.py <host> 1337                   # боевой инстанс (TLS)
```

Оригинальный архив задания остался в `~/Downloads/handout(2).zip`; в папке лежит уже распакованная копия.