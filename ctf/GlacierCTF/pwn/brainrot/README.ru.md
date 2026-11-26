# brainrot — GlacierCTF 2026 (pwn)

[English version](README.md) · [GlacierCTF 2026](../../README.ru.md)

- **Автор:** hweissi
- **Баллы:** 50
- **Теги:** pwn, sweb, brainfuck, qemu
- **Условие:** «I wrote a brainfuck interpreter! I don't know how to sandbox it properly, so i just put it into our educational OS.»
- **Сервис:** `challs.glacierctf.com:13370` (исторический, сейчас не используется)
- **Архив:** `brainrot.tar.gz`
- **Результат:** флаг получен в логе удалённого SWEB; в публичном GitHub он замаскирован.

## Кратко

В обработчике вывода Brainfuck-интерпретатора вызывается `printf(outbuf)` вместо безопасного `printf("%s", outbuf)`. Программа Brainfuck копирует входной payload в вывод, после чего форматная строка меняет ход исполнения. Подготовленный код выполняет системные вызовы SWEB для открытия, чтения и вывода `/usr/flag`.

В логе удалённого сервиса есть настоящий флаг. Позднее при завершении SWEB возникает kernel panic, но флаг к тому моменту уже выведен.

## Файлы

- [solve.ru.md](solve.ru.md) — детальный технический райтап.
- [solve.md](solve.md) — английская версия.
- [scripts/brainrot_solve_reconstructed.py](scripts/brainrot_solve_reconstructed.py) — восстановленный генератор payload для офлайн-проверки.
- [assets/README.md](assets/README.md) — состав оригинального handout и контрольные суммы.
- [evidence/README.md](evidence/README.md) — логи, хеши и ограничения.

Оригинальный солвер утрачен; сохранённая версия восстановлена по переписке. Во время подготовки write-up ничего повторно не эксплуатировалось.
