# Old Trusty (Active Directory, two flags)

[English](README.md) | [Полный ход решения](solve.ru.md)

**Задача:** получить по одному флагу с рабочего стола Administrator на LEGACY DC и CORE DC, связанных межлесным доверием.

**Статус:** **оба флага получены**, что подтверждается исходными материалами. В сохранившемся API-снимке Old Trusty не числится завершённой; сдача обоих флагов платформе отдельно не подтверждена (в публичных файлах `PP{...}`).

Цепочка LEGACY: AS-REP у `jdoe` → BloodHound и ACL (`WriteDacl`, `AddMember`, `AddKeyCredentialLink`) → Shadow Credentials на `svc_ops` → `Server Operators` и управление службой через SCM → NTDS → Administrator → первый флаг.

Цепочка CORE: DRSR/TDO trust-секрет → TGT `LEGACY$` → общая шара `Software$` и `enrollment-agent.xml` → учётная запись `svc_enrollment` → AD CS ESC3 с сертификатом агента и финальным шаблоном `User` → PKINIT как Administrator → второй флаг.

Самая долгая ошибка: `KDC_ERR_PADATA_TYPE_NOSUPP` при использовании финального сертификата с неподходящим EKU. Решение — запросить конечный сертификат именно по `User`, а не повторно использовать Enrollment Agent template.

**Источник:** архив с `OldTrusty/README.md` (полный разбор, 13 КБ), `evidence/FLAG1.txt` и `FLAG2.txt`, Certipy output, XML и Python-скриптами.

[Подробнее: полный разбор на русском](solve.ru.md).