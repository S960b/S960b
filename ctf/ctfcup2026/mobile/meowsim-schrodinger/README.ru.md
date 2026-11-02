# MeowSim: Schrodinger Edition

[English](README.md) | [Путь решения](solve.ru.md)

- **Событие:** отбор CTFCUP 2026
- **Баллы:** 100
- **Категория:** mobile / реверс Android
- **Задача:** https://game.ctfcup.online/tasks/mobile-schrodinger-cat
- **Файл:** `cat.apk`
- **Флаг:** `ctfcup{...}` (скрыт)

APK это небольшое приложение на Kotlin (`ru.ctf.meowsim`) с хранилищем коробки в SQLite, двумя shared preferences и хранилищем флага. `QuantumState.canOpenFlagVault()` требует, чтобы питомец был жив, а коробка одновременно сообщала о смерти кота и суперпозиции, стабильность не меньше 90, отсутствие измерения и отсутствие метки вмешательства.

Лабораторная функция `RestoreExperimentActivity` восстанавливает коробку из снимка независимо от записи наблюдателя, именно это делает состояние достижимым; `stabilize()` даёт стабильность 92. Дальше `FlagVault` выводит ключ SHA-256 из `observerToken | boxSeal | stability | catAlive | catDead | superposition | observed | "meowsim-v1"` и складывает по XOR 23 зашифрованных байта с блоками `sha256(key || be32(counter))`.

`scripts/decrypt_flag.py` без аргументов воспроизводит открытый текст из значений по умолчанию.
