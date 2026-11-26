# Orbital Privacy Screwer (misc) — GlacierCTF 2026

[English version](README.md) · [GlacierCTF 2026](../../README.ru.md)

- **Автор:** Kobalsky
- **Баллы:** 50
- **Условие:** «You better hide well, and you do it fast, because otherwise we will find you, and if we have to go to orbit for it, we will.»
- **Лаунчер:** `nc challs.glacierctf.com 13381` (исторический)
- **Архив:** `orbital-privacy-screwer.tar.gz`
- **Статус:** решено; участник подтвердил принятие собранного флага платформой.

## Идея

Это **стеганография в таймингах SSE**, а не эксплуатация HTTP-сервиса. Дашборд возвращает 67 событий `step`, похожих на обычную телеметрию. Время **между соседними событиями** представляет символы Морзе: 67 мс — точка, 133 мс — тире, 200 мс — разделитель букв.

Шесть отдельных измерений и медиана по каждой позиции восстановили 16-символьное сообщение `GCTF0BLIVTDR3S7T`. Первые четыре символа служат префиксом `gctf`, остальное помещается в фигурные скобки формата задания; полученный флаг подтверждён платформой.

Полный принятый флаг в публичных файлах замаскирован, как и в остальных write-up; исходное переданное сообщение сохранено в техническом разборе.

## Файлы

- [solve.ru.md](solve.ru.md) — технический райтап.
- [solve.md](solve.md) — английская версия.
- [scripts/capture_morse.py](scripts/capture_morse.py) — переносимый вариант захвата шести SSE-сессий.
- [scripts/verify_decode.py](scripts/verify_decode.py) — оригинальная офлайн-проверка из архива агента.
- [assets/README.md](assets/README.md) — исходники задания.
- [evidence/README.md](evidence/README.md) — доказательства и ограничения.

Сервис при написании райтапа повторно не запускался.
