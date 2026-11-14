# Legacy IT

[English](README.md) | [Решение](solve.ru.md)

- **Категория:** ad
- **Сложность:** 1
- **Материалы:** Two saved Impacket SMB commands; no server source or credential-origin transcript
- **Флаги:** 1 принято (`PP{...}`)

Задача про Active Directory и старую схему аутентификации. В конце получили доступ Administrator к файлу на контроллере домена.

**Причина:** В сохранённой команде используется `impacket-smbclient` с NT-хэшем (`-hashes :<скрытый NT-хэш>`), домен `easyad.local` и пользователь `Administrator`. Затем открывается административная шара `C$`, просматривается `Users\Administrator\Desktop` и читается `flag.txt`. Это подтверждает последний шаг. Итоговый отчёт называет причиной legacy-аутентификацию и обратную совместимость, но источника NT-хэша или механизма получения учётных данных в архиве нет.

[Подробный разбор](solve.ru.md).
