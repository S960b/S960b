# Proto Prism (web, protobuf, RCE)

[English version](README.md) · [GlacierCTF 2026](../../README.ru.md)

Автор: schni. Баллы: 50. Исторический сервис: challs.glacierctf.com:13376. Задание преобразует ZIP с каталогом protos/ в Insomnia v4 JSON.

**Решено:** в материалах агента сохранён ответ HTTP 200 с настоящим флагом в поле protoText. **Участник подтвердил принятие флага платформой.** В публичном репозитории флаг скрыт согласно принятому правилу.

Уязвимость — argument injection через response-файлы protoc. Имя protos/@args.proto заставляет компилятор читать args.proto из корня распаковки как список аргументов. В нём задаётся /bin/sh как плагин генерации. Специальное 35-байтовое имя первого proto-файла превращает начало бинарного CodeGeneratorRequest в shell-комментарий. В результате команда переписывает protos/service.proto содержимым /flag, и приложение возвращает этот текст в Insomnia JSON.

Подробности: [solve.ru.md](solve.ru.md), [solve.md](solve.md), [scripts/README.md](scripts/README.md), [evidence/README.md](evidence/README.md). Полный частный архив — glacierctf_proto-prism_materials.zip. Живой сервис повторно не использовался.
