#!/usr/bin/env bash
# Watchdog для 44-часового maker-сбора (PRL/QUANTUS/LTC).
# Запуск из crontab каждые 30 мин:
#   */30 * * * * /home/kali/safetrade-research/scripts/maker_collect_watchdog.sh
# Делает:
#   1. Проверку живости коллектора (pgrep по точному python-аргументу).
#   2. Контрольные агрегаты на 2/6/24/44 часа от старта run (явный cutoff).
#   3. Автостоп при устойчивых проблемах (3 подряд неполных live polls ИЛИ
#      возраст валидного depth > 120с после warmup): стоп-маркер + kill.
#   4. Копирует manifest/summary в reports для проверки без raw.
set -u
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
cd "$PROJECT_DIR" || exit 1
mkdir -p logs reports
exec 9>logs/maker_watchdog.lock
flock -n 9 || exit 0
[ ! -f logs/MAKER_STOP ] || exit 0

VENV_BIN="$PROJECT_DIR/venv/bin/python"
LOG="logs/maker_watchdog.log"
ts() { date -u +%FT%TZ; }

# --- находим последний MAKER run (не старые запуски)
RID=""
for m in $(ls -t data/maker/*_manifest.json 2>/dev/null); do
    rid_candidate=$(basename "$m" _manifest.json)
    # manifest должен содержать PRLUSDT и minutes 2640 (44ч)
    if grep -q 'PRLUSDT' "$m" 2>/dev/null && grep -q '2640' "$m" 2>/dev/null; then
        RID="$rid_candidate"
        break
    fi
done
if [ -z "$RID" ]; then
    echo "$(ts) maker 44h run не найден" >> "$LOG"
    exit 0
fi

# --- 1. живость: python-процесс maker-collect
ALIVE=$(pgrep -f "cli.py maker-collect --pairs PRLUSDT,QUANTUSUSDT,LTCUSDT" | head -1)
if [ -z "$ALIVE" ]; then
    # умер: если summary нет или status != completed — проблемы
    if [ ! -f "data/maker/${RID}_summary.json" ]; then
        echo "$(ts) WARN: коллектор $RID не найден, summary нет" >> "$LOG"
    else
        ST=$(grep -o '"status": *"[a-z]*"' "data/maker/${RID}_summary.json" | head -1)
        echo "$(ts) INFO: коллектор $RID завершился ($ST)" >> "$LOG"
    fi
    exit 0
fi

# --- 2. контрольные точки (мин от старта 44ч run)
START_EPOCH=$(grep -o '"started_epoch": *[0-9.]*' "data/maker/${RID}_manifest.json" | grep -o '[0-9.]*' | head -1)
NOW_EPOCH=$(date +%s)
ELAPSED_MIN=$(( (NOW_EPOCH - ${START_EPOCH%.*}) / 60 ))
for PTS in 120:2h 360:6h 1440:24h 2640:44h; do
    LIMIT_MIN="${PTS%%:*}"
    TAG="${PTS##*:}"
    MARK="reports/${RID}_${TAG}.done"
    if [ "$ELAPSED_MIN" -ge "$LIMIT_MIN" ] && [ ! -f "$MARK" ]; then
        # явный cutoff: последняя строка depth (t в ns)
        CUTOFF=""
        CUTOFF=$(tail -n 1 "data/maker/${RID}_depth.jsonl" 2>/dev/null | grep -o '"t": *[0-9]*' | grep -o '[0-9]*' | head -1)
        if [ -n "$CUTOFF" ]; then
            "$VENV_BIN" maker/detail_analyze.py --run-id "$RID" --data-dir data/maker \
                --cutoff-ns "$CUTOFF" --json-out "reports/pair_screen_maker_${RID}_${TAG}.json" \
                >>"$LOG" 2>&1
            "$VENV_BIN" maker/econ.py --run-id "$RID" --data-dir data/maker \
                --cutoff-ns "$CUTOFF" --json-out "reports/econ_${RID}_${TAG}.json" \
                >>"$LOG" 2>&1
            cp "data/maker/${RID}_manifest.json" "data/maker/${RID}_summary.json" reports/ 2>/dev/null
            echo "$(ts) контрольная точка ${TAG} (${ELAPSED_MIN} мин): агрегат+econ сохранены" >> "$LOG"
            touch "$MARK"
        fi
    fi
done

# --- 3. устойчивые проблемы: 3 ПОДРЯД неполных live polls ИЛИ depth age > 120с
# (92d4079-задание: три подряд неполных live-опроса должны вызвать проверку
# и приостановку с сохранением данных). Проверку ведём одним python-вызовом:
# по каждой паре последние 3 live-полла (warmup=false), все три неполные => стоп.
STOP_REASON=""
STOP_REASON=$("$VENV_BIN" - "$RID" "$PROJECT_DIR" <<'PYEOF'
import json, os, sys, time
rid, root = sys.argv[1], sys.argv[2]
tr_path = os.path.join(root, 'data', 'maker', f'{rid}_trades.jsonl')
dp_path = os.path.join(root, 'data', 'maker', f'{rid}_depth.jsonl')
try:
    rows = [json.loads(l) for l in open(tr_path) if l.strip()]
except FileNotFoundError:
    sys.exit(0)
pairs = [p for p in json.load(open(os.path.join(root,'data','maker',f'{rid}_manifest.json'))).get('pairs',[])]
NONFULL = ('request_failed','history_truncated','coverage_unknown','pagination_not_advancing')
for pair in pairs:
    live = [r for r in rows if r.get('pair')==pair and r.get('warmup') is False]
    if len(live) >= 3 and all(r.get('coverage') in NONFULL for r in live[-3:]):
        print(f'3_notfull_live_{pair}')
        sys.exit(0)
# depth age: последний ok depth
now = time.time()
last_ok = None
try:
    for line in open(dp_path):
        if not line.strip(): continue
        r = json.loads(line)
        if r.get('ok') is True:
            last_ok = (r.get('t') or 0) / 1e9
except (FileNotFoundError, ValueError, TypeError):
    pass
if last_ok is not None and (now - last_ok) > 120:
    print('depth_age>120')
sys.exit(0)
PYEOF
)
if [ -n "$STOP_REASON" ]; then
    echo "$(ts) AUTO-STOP: $STOP_REASON у $RID" >> "$LOG"
    touch logs/MAKER_STOP
    kill "$ALIVE" 2>/dev/null
    exit 0
fi
echo "$(ts) ok: $RID жив, ${ELAPSED_MIN} мин" >> "$LOG"
exit 0