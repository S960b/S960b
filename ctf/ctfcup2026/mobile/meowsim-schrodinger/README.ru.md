# MeowSim: Schrodinger Edition

[English](README.md) | [Подробное решение](solve.ru.md)

- **Соревнование:** CTFCUP 2026
- **Баллы:** 100
- **Категория:** mobile / Android reverse
- **Задание:** https://game.ctfcup.online/tasks/mobile-schrodinger-cat
- **Файл:** `cat.apk`
- **Флаг:** `ctfcup{...}` (скрыт)

В приложении симулируется квантовый кот. Для открытия хранилища нужно получить противоречивое состояние, одновременно «жив» и «мёртв», с высокой стабильностью и без наблюдения. Решение было найдено статическим анализом `classes.dex`, без запуска Android.
