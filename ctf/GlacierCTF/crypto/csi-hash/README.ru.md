# CSI-HASH — GlacierCTF 2026

[English version](README.md) · [GlacierCTF write-ups](../../README.ru.md)

**Автор:** LosFuzzys · **Баллы:** 50 · **Теги:** PQC, hash

> Everyone fears about quantum-computers breaking public-key primitives but no one thinks about hash functions. To fix this gap we introduce the isogenie based hash function CSI-HASH.

**Статус: решено — флаг принят платформой (подтверждено участником).**

Исторический сервис: `nc challs.glacierctf.com 13378`. Оригинальный архив: `csi-hash.tar.gz`.

## Что нашли

32 байта сообщения используются как показатели 32 малых простых идеалов в CSIDH-подобном действии на эллиптических кривых. Результат зависит от класса произведения этих идеалов. Нетрудивиальные целочисленные соотношения в группе классов дают вторые прообразы.

Для поиска исследовали группу классов в PARI/GP, вычисляли дискретные логарифмы идеалов, строили решётку и сокращали её LLL. Соотношения проверяли **в правильном немаксимальном квадратичном порядке**. Финальное решение оказалось очень простым: **вычесть 3 из каждого из 32 байтов**. Получается другая печатная строка той же длины и такой же хеш — это подтверждает реальный ответ сервера.

## Состав

- [solve.ru.md](solve.ru.md) — полный технический райтап.
- [solve.md](solve.md) — английский вариант.
- [scripts/solve.py](scripts/solve.py) — **минимальный рабочий солвер**, без Sage и PARI.
- [scripts/verify_relation.py](scripts/verify_relation.py) — офлайн-проверка по архивным логарифмам группы классов.
- [scripts/README.md](scripts/README.md) — оригинальные скрипты исследования в архиве агента.
- [assets/README.md](assets/README.md) — Sage-исходник и хеши.
- [evidence/README.md](evidence/README.md) — лог успешной TCP-сессии.

Полные исследовательские скрипты PARI/LLL и `csi_logs.json` сохранены в `glacierctf_csi-hash_materials.zip`. Флаг в публичном GitHub замаскирован по принятому формату райтапов.
