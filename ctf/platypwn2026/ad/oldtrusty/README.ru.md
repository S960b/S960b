# Old Trusty (Active Directory, two flags)

[English](README.md) | [Полный ход решения](solve.ru.md)

**Задача:** получить по одному флагу с рабочего стола Administrator на LEGACY DC и CORE DC, связанных межлесным доверием.

**Статус:** **оба флага получены, сданы и приняты платформой; задача Old Trusty засчитана.** Файлы с оригинальными полными флагами опубликованы в `evidence/`.

**Принятые флаги:**

- **LEGACY / FLAG1:** `PP{d4cl-4cls-sh4d0ws-4nd-s3rv1c3s}` — [оригинал `FLAG1.txt`](evidence/FLAG1.txt)
- **CORE / FLAG2:** `PP{trvst-th3-c3rt1f1c4t3-ch41n}` — [оригинал `FLAG2.txt`](evidence/FLAG2.txt)

Цепочка LEGACY: AS-REP у `jdoe` → BloodHound и ACL (`WriteDacl`, `AddMember`, `AddKeyCredentialLink`) → Shadow Credentials на `svc_ops` → `Server Operators` и управление службой через SCM → NTDS → Administrator → первый флаг.

Цепочка CORE: DRSR/TDO trust-секрет → TGT `LEGACY$` → общая шара `Software$` и `enrollment-agent.xml` → учётная запись `svc_enrollment` → AD CS ESC3 с сертификатом агента и финальным шаблоном `User` → PKINIT как Administrator → второй флаг.

Самая долгая ошибка: `KDC_ERR_PADATA_TYPE_NOSUPP` при использовании финального сертификата с неподходящим EKU. Решение — запросить конечный сертификат именно по `User`, а не повторно использовать Enrollment Agent template.

**Доказательства:** исходный подробный разбор, журналы Certipy, XML и Python-скрипты, а также оригинальные файлы [FLAG1.txt](evidence/FLAG1.txt) и [FLAG2.txt](evidence/FLAG2.txt).

[Подробнее: полный разбор на русском](solve.ru.md).