# Russian Post: Trust Level

[English](README.md) | [Путь решения](solve.ru.md)

- **Событие:** отбор CTFCUP 2026
- **Баллы:** 337
- **Категория:** mobile
- **Задача:** https://game.ctfcup.online/tasks/mobile-russian-post
- **Файл:** `sealed-prefs.apk`
- **Флаг:** `ctfcup{...}` (скрыт)

Android-приложение службы доставки, которое получает запечатанный флаг с бэкенда. Клиент меняет `GET /api/config` на `challenge` и `session`, отправляет профиль устройства в `/api/parcel` и получает контейнер, IV и receipt, затем отправляет те же данные вместе с receipt в `/api/key` и получает 16-байтовый ключ.

Видимый путь в Java это приманка: `MainActivity.a()` намеренно отправляет пустой receipt, а рабочий сборщик запроса не вызывается. Нативная функция расшифровывает цепочным XOR, `plain[i] = cipher[i] XOR cipher[i-1] XOR key[i]`, и проверяет PKCS#7. Одна деталь деплоя важна: сервер называет поле контейнера `parcel`, а приложение читает `sealed_parcel`.

`scripts/solve_sealed_prefs.py --base https://<host>` проходит всю цепочку и печатает открытый текст.
