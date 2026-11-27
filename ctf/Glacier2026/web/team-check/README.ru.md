# Team-Check — GlacierCTF 2026

[English version](README.md) · [GlacierCTF 2026](../../README.ru.md)

**Автор:** LosFuzzys · **Баллы:** 50 · **Категория:** web · **Тег:** Beginnerfriendly

> Lately, a lot of teams have started to use AI. We built a small webapplication to check if people use it for CTFs.

**Исторический сервис:** https://teamcheck.web.glacierctf.com/

**Статус: решено — флаг принят платформой (подтверждение участника).**

## Суть

В Flask-приложении строка `teamname` подставлялась в `flask.render_template_string(f"...{team_name}...")`. Наивный фильтр запрещал только `{{` и `}}`, но оставлял Jinja-теги `{% ... %}`. Тег `print` позволял сначала вычислить `7*7`, затем обратиться к `lipsum.__globals__['os']`, выполнить команду от `root` и прочитать `/flag.txt`.

Дополнительно через эту же уязвимость был прочитан `/app/app.py`, подтвердивший причину. Полное значение флага в публичном репозитории скрыто как `gctf{...}`.

## Материалы

- [solve.ru.md](solve.ru.md) — подробный райтап на русском.
- [solve.md](solve.md) — английская версия.
- [scripts/ssti.py](scripts/ssti.py) — оригинальный эксплойт на стандартной библиотеке Python.
- [assets/README.md](assets/README.md) — метаданные CTFd и состояние handout.
- [evidence/README.md](evidence/README.md) — сохранённая страница с 49 и исходник, извлечённый через RCE.

**Ограничение источников:** оригинальный `team-check.tar.gz` не удалось скачать из-за HTTP 502. `app.py` получен через RCE, а не из архива организаторов. Вывод успешной команды чтения флага остался в истории исследования, но отдельного сырого лог-файла нет.
