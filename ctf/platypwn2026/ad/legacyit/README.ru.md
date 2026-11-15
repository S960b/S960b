# Legacy IT

[English](README.md) | [Решение](solve.ru.md)

**Категория:** Active Directory · **Сложность:** 1 · **Статус:** флаг принят платформой

Анонимная SMB-шара `Public/onboarding.txt` раскрыла учётные данные `trainee`. Чрезмерные права `FullControl` на группу `Legacy IT Admins` позволили добавить себя, выполнить DCSync и прочитать флаг Administrator через SMB с NT-хэшем.

Флаг: `PP{l3g4cy-1t-4dm1ns-4k4-w1nd0ws-4dm1ns::FqCiLH_9Sp7u}`.

[Полный разбор](solve.ru.md) · [Доказательства](evidence/session-checkpoints.md)
