# Flagpression (crypto / misc)

[English version](README.md) · [GlacierCTF 2026](../../README.ru.md)

- **Автор:** sc0peMa3str0
- **Баллы:** 50
- **Категория:** арифметическое кодирование
- **Статус:** задача решена; флаг принят платформой, повторно подтверждено участником 26 ноября 2026 года

## Условие

> One of our members got a bit crazy lately. He draws everywhere this number and talks bogus all the time. Lately, we even saw this piece of paper on his desk.

В условии — рукописная модель арифметического кодирования и дробное десятичное число с 202 цифрами после запятой, начинающееся на `0.000976568521066750...`. Нужно восстановить алфавит, понять преобразование и получить флаг.

## Файлы

- [solve.ru.md](solve.ru.md) — технический путь решения и нюансы численной модели.
- [scripts/inspect_decimal.py](scripts/inspect_decimal.py) — проверка исходной дроби и транскрибированного алфавита на точных рациональных числах (не полный солвер).
- [evidence/README.md](evidence/README.md) — происхождение доказательств и ограничения.

Согласно правилам репозитория настоящий флаг в файлах скрыт как `gctf{...}`.
