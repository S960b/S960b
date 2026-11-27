# LosVault — GlacierCTF 2026

[English version](README.md) · [Полный технический райтап](solve.ru.md)

**Автор:** blissnox · **Категория:** web · **Баллы:** 50  
**Статус:** решено — флаг принят платформой (подтверждение участника).

> Ever wanted a digital vault to store your valuable documents? Now you can, with LosVault! We even have a transparency report and review your files! Can you crack the vault to uncover its secret?

Адрес задания: https://losvault.web.glacierctf.com

## Цепочка атаки

Review-бот обращается к внутренней службе восстановления по HTTPS на `127.0.0.1:9446`; TLS-захват доступен владельцу записи evidence. В утекшем `suspicious_note.md` обнаружен генератор RSA, создающий `q` рядом с `p + 2^240`, а не выбирающий независимые простые числа.

**Внутренний RSA-1024** факторизуется методом Ферма **за одну итерацию**. Сессия бота использует `TLS_RSA_WITH_AES_128_GCM_SHA256`: старый RSA key exchange без PFS. Приватный ключ позволяет расшифровать записанный TLS, прочитать `proof` во внутреннем recovery-ответе и отправить этот integrity-код в `POST /vault/evidence/<id>/verify`, который возвращает флаг.

Полная цепочка, команды, проверки и происхождение каждого этапа — в [solve.ru.md](solve.ru.md) и [solve.md](solve.md).

**Источники:** `glacierctf_losvault_materials.zip` содержит заметку предыдущей сессии, утечку кода, сертификат, три PCAP, JSON evidence, сохранённый proof и скрипты. Факторизация повторно проверена при упаковке. Расшифрованный HTTP и финальный ответ описаны предыдущей сессией, но **не были повторно воспроизведены** при упаковке.

Полный принятый флаг хранится в частном архиве; публичный GitHub использует маску `gctf{...}`.
