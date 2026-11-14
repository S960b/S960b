# Fishies

[English](README.md) | [Решение](solve.ru.md)

- **Категория:** web
- **Сложность:** 3
- **Материалы:** `server.js`, package files, Handlebars template; saved later successful download evidence and earlier Git-bridge investigations
- **Флаги:** 1 принято (`PP{...}`)

Приложение для обмена рыбками и бот на Puppeteer. Цепочка объединила XSS, небезопасное рекурсивное слияние объектов и наследование настроек запуска браузера.

**Главная ошибка:** Самописный `escape()` меняет `<` и `>` на длинные HTML-entities внутри строки, но после замены слишком сильно увеличивает индекс. Соседние скобки позволяют оставить рабочий HTML-тег. `POST /api/fish/:id/report` запускает Puppeteer с HttpOnly-cookie администратора. Выполненный ботом JS может вызвать защищённый `PATCH /api/admin/fish/:id/fishData`. Его `combine(target,source)` не фильтрует `__proto__`, позволяя менять `Object.prototype`. Аудит Puppeteer 25.16.0 подтвердил, что Chromium получает унаследованные `env`-настройки.

Подробнее — в [решении](solve.ru.md).
