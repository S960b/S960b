# LosVault — слабый RSA во внутреннем TLS и восстановление integrity-кода

[English version](solve.md)

## 1. Источники и проверяемость

Это **решённая пользователем задача**, флаг принят платформой. Полная цепочка взята из **сохранённой заметки предыдущей сессии** (`evidence/previous_session_writeup.md` внутри `glacierctf_losvault_materials.zip`) и сверена с исходной утечкой генератора RSA, сертификатом, PCAP, JSON evidence и Python-скриптом Ферма.

Важное разграничение: агент, собиравший ZIP, **не эксплуатировал сервис заново**. Он лично повторил факторизацию внутреннего RSA и проверил наличие артефактов. Расшифрованный HTTP, найденный proof и окончательный ответ с флагом — результаты исторической сессии, не повторённые при упаковке.

## 2. Архитектура и неудачные пути

LosVault — Flask-приложение с регистрацией, загрузкой документов, отчётом прозрачности и ботом проверки файлов. Чужие файлы отвечали 404; административные URL `/api/admin/transparency/export`, `/api/admin/capture-tls/<id>` и `/admin/review/<id>` требовали **серверной роли `vault-audit`**. Изменение имени пользователя, XSS, попытки серверного рендера, callback/SSRF и операции с очередью не дали прямого доступа.

Сохранились исследовательские скрипты `losvault_xss_test.py`, `losvault_xss2.py`, `losvault_render_test.py`, `losvault_cb_test.py`, `los_ssrf_test.py`, `los_export_test.py`, `los_delbatch.py`. Важно: `los_capture_note.py` содержит несколько XSS-вариантов note, которые вызывают `/api/admin/capture-tls/<id>`. **Нельзя выдавать сам этот скрипт за подтверждённую финальную загрузку, заставившую бота сделать recovery.**

## 3. Получение TLS-захвата

По **записи реально успешной предыдущей сессии**, review-бот обрабатывал файлы FIFO и, встретив в загруженной note внутренний URL, выполнял свой HTTPS-запрос к внутренней recovery-службе. Владелец записи затем мог получить:

```text
GET /api/evidence/<entry_id>
→ {"tls_capture": {"download_url": "/vault/evidence/<entry_id>/tls.pcap", ...}}
```

В частном архиве сохранены **три разных PCAP по 2211 байт** для entry **1285, 1286, 1287** и три JSON-ответа API. Каждый JSON содержит 200 записей и метаданные TLS c `TLS_RSA_WITH_AES_128_GCM_SHA256`.

**Точная HTTP-загрузка note, запустившая окончательную успешную цепочку, не сохранилась.** Поэтому описывается документированный механизм, а не вымышленный конкретный payload.

## 4. Уязвимый генератор RSA

Из `assets/suspicious_note.md`, полученного на самом сервисе, а не из исходного handout организаторов:

```python
p = _generate_prime(512, 65537)
candidate = (p + (1 << 240)) | 1
# затем поиск ближайшего простого числа q
```

Вместо независимых 512-битных простых выбирается `q≈p+2^240`. Для `N=pq` достаточно метода Ферма:

```text
a = ceil(sqrt(N))
b² = a² − N
p=a−b, q=a+b   (когда b² полный квадрат)
```

Сохранённый оригинальный [losvault_fermat.py](scripts/losvault_fermat.py) извлекает `N,e` из сертификата и рассчитывает `d=e^(-1) mod lcm(p−1,q−1)`. В независимом прогоне при упаковке:

```text
RSA modulus bit length: 1024, exponent: 65537
Factors found in 1 Fermat iteration(s)
RSA key ok
```

Это сертификат **внутреннего TLS** с `CN=localhost`, а не обычный внешний сертификат Let's Encrypt RSA-2048.

## 5. Расшифровка PCAP

Внутренняя recovery-служба работает на `127.0.0.1:9446`. Сессия бота использовала `TLS_RSA_WITH_AES_128_GCM_SHA256`: RSA key exchange **без PFS**. Если получить закрытый RSA-ключ сервера, можно расшифровать ранее записанный трафик.

Команды из отчёта агента:

```sh
tshark -r tls.pcap -Y tls.handshake.certificate \
  -T fields -e tls.handshake.certificate | tr -d ':' | xxd -r -p > internal_cert.der
openssl x509 -inform DER -in internal_cert.der -out internal_cert.pem

python3 scripts/losvault_fermat.py internal_cert.pem --out vault_internal.key
openssl rsa -in vault_internal.key -check -noout

tshark -r tls.pcap \
  -o "tls.keys_list:127.0.0.1,9446,http,./vault_internal.key" \
  -d tcp.port==9446,http \
  -T fields -e http.request.method -e http.request.uri \
  -e http.response.code -e http.file_data
```

Предыдущая сессия получила:

```text
GET /internal/recovery?entry_id=<id>
ответ: proof=<integrity-code>
```

В `evidence/proof.txt` частного ZIP хранится конкретный код, совпадавший у нескольких entry. В GitHub он намеренно **не печатается**, поскольку это секрет, предназначенный для финального POST.

При упаковке повторно не запускали `tshark`. Сами PCAP, сертификат, скрипт восстановления ключа, сохранённый proof и лог независимой факторизации присутствуют.

## 6. Проверка и флаг

На странице evidence существовала форма **RECORD INTEGRITY**. В неё передавался код:

```http
POST /vault/evidence/<entry_id>/verify
Content-Type: application/x-www-form-urlencoded

recovery_code=<proof>
```

Сохранённая предыдущая сессия фиксирует ответ **«Vault integrity confirmed»** и выдачу флага. Флаг **принят платформой** по подтверждению пользователя. Сырой HTTP-ответ на финальный POST и cookie-файл сессии в ZIP отсутствуют; их нельзя приписывать повторному прогону.

## 7. Почему получилось

Это комбинированная ошибка из четырёх частей: зависимые RSA-простые, старый TLS без forward secrecy, раскрытие записанного внутреннего TLS-трафика владельцу evidence и использование восстановленного proof как ключа к секретному файлу.

Адекватная защита: независимая генерация RSA-простых, TLS 1.3/ECDHE, изоляция захватов служебного трафика и отказ от многоразовых bearer-подобных кодов подтверждения.

## 8. Приложения и честность доказательств

`glacierctf_losvault_materials.zip` содержит `REPORT.md`, исходный заметочный `previous_session_writeup.md`, `suspicious_note.md`, PEM/DER, три PCAP, три больших JSON evidence, `proof.txt`, скрипты и `verification_fermat.txt`. Публичный GitHub содержит методы и команды, **но не приватный proof, cookie либо полный флаг**.

Агент лично проверил RSA; расшифровка и финальный POST восстановлены по прежней сессии и не объявляются повторно выполненными.
