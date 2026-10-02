#!/usr/bin/env bash
# Run from cron; lock belongs to the collector for its full lifetime.
set -u
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
cd "$PROJECT_DIR" || exit 1
mkdir -p logs
exec 9>logs/collector.lock
flock -n 9 || exit 0
[ ! -f logs/COLLECTOR_STOP ] || exit 0
if ! ./venv/bin/python cli.py collect --symbols BTCUSDT >>logs/collect_btc.log 2>&1; then
    touch logs/COLLECTOR_STOP
    printf '%s collector failed; inspect logs/collect_btc.log\n' "$(date -u +%FT%TZ)" >>logs/watchdog.log
fi
