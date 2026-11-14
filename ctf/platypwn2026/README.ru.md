# Platypwn 2026

[English](README.md)

- **Даты:** 13–14 ноября 2026 года
- **Формат:** онлайн CTF с отдельными задачами
- **Платформа:** https://platypwn.ctf.platypwnies.de/
- **Команда:** SOC²
- **Итоговое место:** 49-е (по окончательному результату команды). Промежуточный снимок API показывал 54-е место, 3957 очков и 24 флага; это не финальная позиция.
- **Формат флага:** `PP{...}`

## Райтапы

Здесь **18 технических разборов**, подготовленных по исходникам задач, сохранённым журналам, восстановленным солверам и заметкам с соревнования. В Old Trusty получены оба флага, но задача отсутствует среди завершённых в сохранившемся API-снимке: выдавать её за подтверждённую сдачу на платформе по этому снимку нельзя. Flacksafety закрыта частично: уничтожены пять из шести уровней резервного копирования, получен governance-флаг.

Формат совпадает с другими соревнованиями в портфолио:

- `README.md` и `solve.md`: описание и пошаговое решение на английском
- `README.ru.md` и `solve.ru.md`: русские версии
- `scripts/`: сохранившиеся и отдельно помеченные восстановленные скрипты
- `assets/` и `evidence/`: исходники, доступные файлы задания, очищенные логи

Полные флаги, пароли, NT-хэши и адреса инстансов не публикуются. Если код или вывод команды не сохранился, это отмечено. Незавершённые стадии не выдаются за успех.

## Задачи

| Категория | Задача | Что исследовали |
|---|---|---|
| Reverse | [Matryoshka](rev/matryoshka/solve.ru.md) | CRC32, линейные уравнения, XOR |
| Reverse | [FSenc](rev/fsenc/solve.ru.md) | ChaCha20-Poly1305 и Argon2id |
| Crypto | [Unholy Trinity](crypto/unholytrinity/solve.ru.md) | Коллизия RSA-хэша, целочисленный корень |
| Crypto | [Ron's Revenge](crypto/ronsrevenge/solve.ru.md) | Chosen-IV RC4-2048 |
| Crypto | [DEStruction](crypto/destruction/solve.ru.md) | GHASH, короткий тег, два флага |
| Web | [Horse Racing](web/horseracing/solve.ru.md) | Гонка с задержанным HTTP-телом |
| Web | [Fishies](web/fishies/solve.ru.md) | XSS, prototype pollution, Puppeteer, LD_PRELOAD |
| Blockchain | [Just in Time](blockchain/justintime/solve.ru.md) | Предсказуемость данных деплоя |
| Blockchain | [PlatyLocker](blockchain/platylocker/solve.ru.md) | Приватный RSA-ключ в storage |
| Blockchain | [Not My Type](blockchain/notmytype/solve.ru.md) | Несоответствие типов при проверке |
| Blockchain | [Referral](blockchain/referral/solve.ru.md) | Коллизия слотов прокси |
| Blockchain | [Card Counting](blockchain/cardcounting/solve.ru.md) | Перекос раздачи и PRNG |
| Blockchain | [PlatyPals](blockchain/platypals/solve.ru.md) | Откат callback и повторный бросок |
| Active Directory | [Legacy IT](ad/legacyit/solve.ru.md) | Финальное чтение SMB с NT-хэшем |
| Active Directory | [Old Trusty](ad/oldtrusty/solve.ru.md) | Цепочка LEGACY → CORE, оба флага получены |
| Fullpwn | [Carbon](fullpwn/carbon/solve.ru.md) | Портал, user и root |
| Fullpwn | [Flacksafety](fullpwn/flacksafety/solve.ru.md) | Пять из шести уровней, частичное решение |
| Misc | [Bish Bash Bosh](misc/bishbashbosh/solve.ru.md) | Арифметика Bash и подстановка команд |

Райтапы подготовлены после завершения соревнования.
