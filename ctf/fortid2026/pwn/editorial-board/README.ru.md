# Editorial Board - ASCII-shellcode под валидатором английского языка

- **Платформа:** FortiD CTF 2026
- **Категория:** Pwn / Binary Exploitation (i386)
- **Автор:** (не зафиксирован в рабочей папке)
- **Очки / Решили:** (не зафиксированы в рабочей папке)
- **Дата:** 2026-10-13
- **Remote:** `ncat --ssl <host>:1337` (TLS; инстанс выдаёт платформа)
- **Флаг:** `FortID{...}` (замаскирован)
- **Артефакт:** `handout(6).zip` - `editorial_board` (32-bit ELF, stripped) + `validate.py`

## Что в задаче

Бинарь требует одну английскую «рукопись» (<=900 байт), проверяет её своим `validate.py` (грейдер на LanguageTool, «anti-AI-slop»), и если текст принят - копирует его в RWX-регион и **без проверки границ** переливает в стековый буфер через `rep movsb` (`sub esp,0x259`, смещение до saved EIP = **605**).

То есть полезная нагрузка должна быть одновременно:

1. грамотным английским текстом, который проходит грейдер (score >= 88, 0 орфоошибок),
2. исполняемым эксплойтом, состоящим только из `A-Za-z` и ` .,;:'-?!`.

Отсюда весь трюк: эксплойт написан **самомодифицирующимся printable-ASCII shellcode i386**, вшитым в осмысленный абзац.

Две ключевые находки:

- **CS-префиксная запись.** Одна из самопатчащих записей декодировалась как `2e 00 41 xx` = `add byte ptr cs:[ecx+xx], al`. На нативном x86 запись в `CS` даёт `#GP`, а `qemu-i386` это не проверяет - поэтому эксплойт «работал» локально и молча падал на сервере. Убрали CS-префикс и скомпенсировали адрес двумя `inc ecx`.
- **Редакторские требования.** Запятая в прологе давала `COMMA_PARENTHESIS_WHITESPACE`, слитные токены вида `.RABPAX` - `MORFOLOGIK_RULE_EN_US`. Помог бескоммовый пролог ` AJ AR AZ-Sugar:` и разбиение блока на короткие токены точками: `.R.A.BA` - score **89.45**, spelling 0, issues 4 - **ACCEPT**.

Эксплойт двухэтапный: stage 1 делает `read(0, EIP+6, 115)`, stage 2 (115 байт) - ORW `open("/flag") - read - write`.

Полный разбор - в [`solve.md`](solve.md).

## Структура папки

```text
pwn/editorial-board/
├── README.md          # это файл: условие задачи
├── solve.md           # решение: разбор, ловушки, скрипты, флаг
├── assets/            # из хендаута
│   ├── editorial_board   # 32-bit ELF, stripped
│   └── validate.py       # оригинальный грейдер
├── scripts/           # наши скрипты
│   ├── editorial_stage9_shorttokens.py    # финальный: рукопись + stage 2 + отправка
│   ├── editorial_stage9_commafree.py      # предыдущая итерация, ORW-билдер
│   ├── editorial_native_grammar_repair.py # CS-free фикс, печатает маркер
│   ├── editorial_native_cs_fix.py
│   └── editorial_board_live_solver.py     # диагностика/тайминги
└── evidence/          # сырые артефакты прогона
    ├── baseline_stage8_accepted_899.txt   # принятая база для патча
    ├── manuscript_accepted_899.txt        # принятая рукопись (899 Б)
    ├── stage2_marker.bin                  # stage 2: маркер
    ├── remote_marker_probe.raw            # ответ сервера с маркером
    └── remote_flag_orw.raw                # ответ сервера с флагом
```

## Воспроизведение

Полностью повторить атаку можно только на живом инстансе (он закрыт после соревнования), но сборку рукописи и payload'ов можно проверить офлайн - `build()` детерминирован:

```bash
# финальный прогон (нужен живой хост, LanguageTool и validate.py рядом)
PATH="$PWD/bin_shim:$PATH" ./venv/bin/python scripts/editorial_stage9_shorttokens.py \
  --baseline evidence/baseline_stage8_accepted_899.txt \
  --host editorial-board-<INSTANCE>.inst2.ctf.fortid.com --limit 400 --deadline 85
```

Проверено офлайн (сошлось побайтово): `build(baseline, 'Sugar', 'rain', b'.R.A.BA')` воспроизводит `evidence/manuscript_accepted_899.txt`, а `marker_stage2()` - `evidence/stage2_marker.bin`. В обоих файлах `message[605:609] = b"able"` - EIP = `0x656c6261` в RWX-регионе (см. `solve.md`).

## Чего в папке нет

`venv/` с `language_tool_python` и Java-обвязкой LanguageTool (десятки мегабайт) - он нужен только для полного прогона, а не для чтения решения. Инстанс мёртв, поэтому полный end-to-end в папке не зафиксирован; доказательства - в `evidence/*.raw`.