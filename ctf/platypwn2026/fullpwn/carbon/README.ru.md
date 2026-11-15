# Carbon

[English](README.md) | [Полный разбор](solve.ru.md)

**Категория:** Fullpwn · **Сложность:** 1 · **Результат:** оба флага приняты

Цепочка: десериализация React Server Components в Next.js 15.5.6 → выполнение кода под `nextjs` → заметки ops → токен `session:operator` из Redis → пароль через локальный admin API → `su operator` → sudo NOPASSWD для `/usr/bin/env` → root.

**User:** `PP{c4rb0n_c4ch3d_s3…Kt36U}`  
**Root:** `PP{0p3r4t0r_3nv_1s_…_dtEE}`

[Разбор решения](solve.ru.md) · [Фрагменты журнала](evidence/session-checkpoints.md)
