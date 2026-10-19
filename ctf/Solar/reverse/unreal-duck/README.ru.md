# Невероятная утка (reverse)

Задача про игру на Unreal Engine. Дано: Windows-сборка клиента, папка контейнеров контента
и лаунчер. Флаг выдаёт игровой сервер после определённых действий в игре.

Что в сборке:

- `UnrealDuckClient.exe` около 167 КБ, это лаунчер, он только запускает основной процесс.
- `UnrealDuck/Binaries/Win64/UnrealDuckClient-Win64-Shipping.exe` около 143 МБ, здесь
  основной код игры.
- `UnrealDuck/Content/Paks/` с контейнерами `global.utoc`, `global.ucas`,
  `UnrealDuck-WindowsClient.utoc`, `UnrealDuck-WindowsClient.ucas` и
  `UnrealDuck-WindowsClient.pak`. Индекс IoStore начинается с шестнадцатибайтового
  текстового баннера, дальше идёт обычный заголовок версии 8.

Опорные факты:

- Контент упакован в IoStore со сжатием Oodle: 2126 chunk, 504 пакета, pak на 1662 файла.
- Пакеты имеют `legacy: -9` и `uses_unversioned_properties: True`, то есть это UE5 и имена
  свойств в файлы не записаны.
- Игровая логика в нативном коде: в строках исполняемого файла есть RPC-функции
  `ServerPushCheatLetter`, `ServerRecordFrame` и `ClientReceivePayload`.
- Контроллер игрока хранит массив букв кода по смещению `0x808`, счётчик по `0x810`, байт
  разблокировки по `0x800`; номер маршрута зоны лежит по смещению `0x2c0`.
- Готовых строк кодов в контенте нет, они проверяются сервером.

Файлы:

- `scripts/duck_extract.sh` - распаковка IoStore и pak
- `scripts/duck_map_layout.py` - разбор структуры классического пакета Unreal
- `scripts/duck_watch_all.py` - перехваты Frida на RPC-функции
- `scripts/duck_capture_payload2.py` - перехват и сохранение полезной нагрузки с флагом
- `scripts/duck_auto_guide.py` - автоматическое прохождение
- `assets/` - список путей контейнера, список ассетов проекта и индексатор IoStore

Тяжёлые артефакты (сама сборка и распакованный контент) в репозиторий не копируются.

Полный путь решения описан в `solve.ru.md`.
