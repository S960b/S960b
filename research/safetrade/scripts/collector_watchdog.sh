#!/bin/bash
# Watchdog v2: держит коллектор живым. flock от гонок; НЕ перезапускает после
# остановки по диску (маркер). Переживает сессии.
# crontab: */5 * * * * /home/kali/safetrade-research/scripts/collector_watchdog.sh
cd /home/kali/safetrade-research || exit 1
mkdir -p logs

exec 9>/tmp/safetrade_watchdog.lock
flock -n 9 || exit 0   # уже работает копия watchdog

# стоп-маркер (остановка по диску/вручную): не воскрешать
if [ -f logs/COLLECTOR_STOP ]; then
    exit 0
fi

if ! pgrep -f '[v]env/bin/python cli.py collect' > /dev/null 2>&1; then
    setsid nohup ./venv/bin/python cli.py collect --symbols BTCUSDT \
        >> logs/collect_btc.log 2>&1 < /dev/null &
    echo "$(date -u +%FT%TZ) watchdog: collector restarted (pid $!)" >> logs/watchdog.log
fi

FREE_GB=$(df -Pk /home | awk 'NR==2 {print int($4/1024/1024)}')
if [ "$FREE_GB" -lt 10 ]; then
    pkill -f '[v]env/bin/python cli.py collect'
    echo "$(date -u +%FT%TZ) watchdog: killed collector (disk <10GB)" >> logs/watchdog.log
    touch logs/COLLECTOR_STOP
fi