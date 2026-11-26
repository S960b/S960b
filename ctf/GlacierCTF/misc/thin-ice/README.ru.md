# thin-ice (Git protocol oracle)

[English version](README.md) · [GlacierCTF 2026](../../README.ru.md)

- **Автор:** ansa
- **Баллы:** 50 (в текущей карточке; в старом отчёте агента было 136)
- **Теги:** git, protocol, oracle
- **Исторический сервис:** `challs.glacierctf.com:13372`
- **Исходный архив:** `thin_ice.tar.gz`
- **Статус:** решена; сдача подтверждена платформой.

> The ice is thin - pack light.

## Кратко

В bare Git-репозитории лежит `flag.txt`. Обычное чтение блокируется: `upload-pack` и `upload-archive` отключены. При этом разрешён `receive-pack`, но `hooks/update` запрещает изменение любых refs.

Решение использует **тонкие Git-паки и REF_DELTA**: диагностика числа неразрешённых дельт позволяет раскрывать по одному байту скрытого объекта. За одно 600-секундное окно солвер прошёл цепочку **commit → tree → blob → flag** и восстановил 27 байт.

Флаг принят платформой; его содержимое в публичном репозитории маскируется как `gctf{...}`.

## Содержимое

- [solve.ru.md](solve.ru.md) — подробный технический райтап.
- [solve.md](solve.md) — английская версия.
- [scripts/verify_git_chain.py](scripts/verify_git_chain.py) — проверка совпадения SHA-1 объектов Git с удалённым адвертайзментом.
- [scripts/README.md](scripts/README.md) — файлы исходного рабочего солвера из архива.
- [assets/README.md](assets/README.md) — описание оригинального задания.
- [evidence/README.md](evidence/README.md) — логи и ограничения доказательств.

При подготовке райтапа живой сервис не использовался.
