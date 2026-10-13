# End of Life - 16-битный DOS, стековый смоук, шеллкод через far-возврат

- **Платформа:** FortiD CTF 2026
- **Категория:** Pwn
- **Автор:** ante
- **Очки / Решения:** 115 очков, 172 решения
- **Дата:** 2026-10-10
- **Remote:** `end-of-life-<...>.inst20.ctf.fortid.com:1337` (TLS/ncat, закрыт после события)
- **Флаг:** `FortID{...}` (замаскирован)
- **Файлы:** `CHALL.CPP` (исходник Borland C++), `CHALL.EXE` (16-битный MZ DOS), `RUN.BAT`, `server.py`
- **Описание (официальное):** "MS Windows 10 has reached its (endoflife.date), but MS DOS is still going strong."

Сервис принимает файл (base64) как INPUT.TXT, запускает `CHALL.EXE` под DOSBox рядом с `FLAG.TXT` (копия настоящего флага) и возвращает base64 того, что программа записала в OUTPUT.TXT. У DOS-программы два бага: текстовый валидатор останавливается на байте 0x1A (Ctrl-Z), пока бинарный парсер читает весь файл, а `fscanf("%[^ ]", name[17])` переполняет стек без ограничения длины. Эксплойт: обход Ctrl-Z, переполнение near-возврата существующим гаджетом `retf`, far-возврат в позиционно-независимый 8086-шеллкод на стеке (INT 21h), который копирует FLAG.TXT в OUTPUT.TXT.

Файлы:

- `solve.md` - полный ход решения, включая dead ends (английский)
- `solve.ru.md` - тот же ход решения на русском (рабочая копия)
- `scripts/calibrate.py` - пропатченные копии печатают живой BP/SS через собственный fprintf программы
- `scripts/build_payload.py` - собирает payload переполнения (INPUT.TXT)
- `scripts/solve.py` - remote-клиент