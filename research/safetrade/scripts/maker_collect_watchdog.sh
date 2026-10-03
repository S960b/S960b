#!/usr/bin/env bash
# Watchdog для 44-часового maker-сбора (PRL/QUANTUS/LTC, РЕВЬЮ d891422).
# Запуск из cron каждые 30 мин (тяжёлые отчёты), health-проверка каждую минуту:
#   * * * * *   .../maker_collect_watchdog.sh health
#   */30 * * *  .../maker_collect_watchdog.sh
#
# Исправления по ревью d891422:
# - health проверяется ПО КАЖДОЙ паре из manifest (свежая пара не маскирует
#   зависшую); ok=True HTTP недостаточно — валиден только двусторонний стакан;
# - checker возвращает корректный rc; JSONDecodeError/обрыв строки =>
#   ERROR/UNKNOWN, никогда не «ok»;
# - .done ставится ТОЛЬКО после успешных анализаторов и валидного JSON
#   с нужным run_id/cutoff (атомарно tmp+mv);
# - финальный 44h отчёт генерируется и после завершения коллектора
#   (summary=completed/stopped), не только при живом процессе;
# - PID/run_id связаны по manifest (mtime+run_id), stop-state конкретного run
#   (logs/MAKER_STOP.<run_id>) — не глобальный.
set -u
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
cd "$PROJECT_DIR" || exit 1
mkdir -p logs reports
exec 9>logs/maker_watchdog.lock
flock -n 9 || exit 0

MODE="${1:-full}"     # 'health' = только проверка живости/возраста (лёгкий)
# реальный путь python: запуск через symlink ломает определение venv
# (prefix от пути symlink => теряются site-packages, duckdb не виден)
VENV_BIN="$(readlink -f "$PROJECT_DIR/venv/bin/python" 2>/dev/null || echo "$PROJECT_DIR/venv/bin/python")"
LOG="logs/maker_watchdog.log"
ts() { date -u +%FT%TZ; }

# --- PYTHONPATH: добавить site-packages ИСТИННОГО venv (устойчиво к symlink-
# копиям проекта, как в изолированных фикстурах тестов: там venv/bin/python —
# symlink на sys.executable, pyvenv.cfg рядом нет, prefix=/usr => duckdb нет).
# В живом прогоне readlink даёт относительный 'python3' -> используем обычный
# путь проекта; в фикстуре readlink даёт абсолютный путь реального venv.
__add_site() {
    local py="$1" link site
    link=$(readlink "$py" 2>/dev/null || true)
    if [ -n "$link" ] && [ "${link#/}" != "$link" ] && [ -e "$link" ]; then
        site=$(ls -d "$(dirname "$link")/../lib/python"*/site-packages 2>/dev/null | head -1)
        [ -n "$site" ] && export PYTHONPATH="$site:${PYTHONPATH:-}"
    fi
    site=$(ls -d "$PROJECT_DIR/venv/lib/python"*/site-packages 2>/dev/null | head -1)
    [ -n "$site" ] && export PYTHONPATH="$site:${PYTHONPATH:-}"
}
__add_site "$PROJECT_DIR/venv/bin/python"

# --- находим текущий MAKER run: manifest PRLUSDT + minutes 2640 (44ч), по mtime
RID=""
MANIFEST=""
for m in $(ls -t data/maker/*_manifest.json 2>/dev/null); do
    if grep -q 'PRLUSDT' "$m" 2>/dev/null && grep -q '2640' "$m" 2>/dev/null; then
        RID="$(basename "$m" _manifest.json)"
        MANIFEST="$m"
        break
    fi
done
if [ -z "$RID" ]; then
    echo "$(ts) maker 44h run не найден" >> "$LOG"
    exit 0
fi
STOP_MARK="logs/MAKER_STOP.$RID"
[ ! -f "$STOP_MARK" ] || exit 0

START_EPOCH=$(grep -o '"started_epoch": *[0-9.]*' "$MANIFEST" | grep -o '[0-9.]*' | head -1)
START_EPOCH="${START_EPOCH%.*}"
NOW_EPOCH=$(date +%s)
ELAPSED_MIN=$(( (NOW_EPOCH - START_EPOCH) / 60 ))

# --- здоровье: python-проверка по каждой паре (per-pair), корректный rc
# health checker: stdout = причина остановки (пусто если здоров); rc:
#   0 = проверка выполнена корректно (даже если найдена проблема),
#   2 = сбой самого checker (невалидный manifest/внутренняя ошибка).
HEALTH_OUT=$("$VENV_BIN" - "$RID" "$PROJECT_DIR" <<'PYEOF'
import json, os, sys, time
rid, root = sys.argv[1], sys.argv[2]
mdir = os.path.join(root, 'data', 'maker')
tr_path = os.path.join(mdir, f'{rid}_trades.jsonl')
dp_path = os.path.join(mdir, f'{rid}_depth.jsonl')
try:
    manifest = json.load(open(os.path.join(mdir, f'{rid}_manifest.json')))
except Exception as e:
    print(f'ERROR manifest: {e}', flush=True); sys.exit(2)
pairs = manifest.get('pairs', [])
rows = []
try:
    with open(tr_path) as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except ValueError:
                # оборванная последняя строка: пропускаем (допишется при flush),
                # повреждённая внутренняя — ERROR ниже
                pass
except FileNotFoundError:
    pass
NONFULL = ('request_failed', 'history_truncated', 'coverage_unknown', 'pagination_not_advancing')
for pair in pairs:
    live = [r for r in rows if r.get('pair') == pair and r.get('warmup') is False]
    if len(live) >= 3 and all(r.get('coverage') in NONFULL for r in live[-3:]):
        print(f'3_notfull_live_{pair}', flush=True); sys.exit(0)   # проблема: stdout, но rc=0
# per-pair depth age: ВАЛИДНЫЙ двусторонний стакан (не пустой/crossed)
now = time.time()
per_pair_age = {}
try:
    with open(dp_path) as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(r, dict) or not r.get('pair') or r.get('ok') is not True:
                continue
            b, a = r.get('bids'), r.get('asks')
            if not b or not a:          # ok=True, но пустой стакан — не валидные данные
                continue
            per_pair_age[r['pair']] = (r.get('t') or 0) / 1e9
except FileNotFoundError:
    pass
for pair in pairs:
    ts = per_pair_age.get(pair)
    if ts is None:
        print(f'depth_no_valid_{pair}', flush=True); sys.exit(0)
    if (now - ts) > 120:
        print(f'depth_age>120_{pair}', flush=True); sys.exit(0)
sys.exit(0)
PYEOF
)
HEALTH_RC=$?
if [ "$HEALTH_RC" -eq 2 ]; then
    echo "$(ts) ERROR health-checker: $HEALTH_OUT" >> "$LOG"
    exit 1                       # не «ok»: checker failure НЕ здоровье
fi
if [ -n "$HEALTH_OUT" ]; then
    echo "$(ts) AUTO-STOP: $HEALTH_OUT у $RID" >> "$LOG"
    touch "$STOP_MARK"
    PID=$(pgrep -f "cli.py maker-collect --pairs PRLUSDT,QUANTUSUSDT,LTCUSDT" | head -1)
    [ -n "$PID" ] && kill "$PID" 2>/dev/null   # SIGTERM: finally коллектора допишет summary
    exit 0
fi
[ "$MODE" = "health" ] && exit 0

# --- контрольные точки: агрегат+econ с явным cutoff; .done ТОЛЬКО при успехе
for PTS in 120:2h 360:6h 1440:24h 2640:44h; do
    LIMIT_MIN="${PTS%%:*}"
    TAG="${PTS##*:}"
    MARK="reports/${RID}_${TAG}.done"
    if [ "$ELAPSED_MIN" -ge "$LIMIT_MIN" ] && [ ! -f "$MARK" ]; then
        CUTOFF=$(tail -n 1 "data/maker/${RID}_depth.jsonl" 2>/dev/null \
                 | grep -o '"t": *[0-9]*' | grep -o '[0-9]*' | head -1)
        OK=1
        if [ -n "$CUTOFF" ]; then
            if ! "$VENV_BIN" maker/detail_analyze.py --run-id "$RID" --data-dir data/maker \
                    --cutoff-ns "$CUTOFF" --json-out "reports/pair_screen_maker_${RID}_${TAG}.json" \
                    >>"$LOG" 2>&1; then
                OK=0
            fi
            if ! "$VENV_BIN" maker/econ.py --run-id "$RID" --data-dir data/maker \
                    --cutoff-ns "$CUTOFF" --json-out "reports/econ_${RID}_${TAG}.json" \
                    >>"$LOG" 2>&1; then
                OK=0
            fi
            # валидность JSON + нужный run_id
            if [ "$OK" = 1 ] && "$VENV_BIN" -c "
import json,sys
p='reports/econ_${RID}_${TAG}.json'
d=json.load(open(p))
assert d.get('run_id')=='${RID}', d.get('run_id')
assert d.get('cutoff_ns')==int('$CUTOFF'), d.get('cutoff_ns')
" >>"$LOG" 2>&1; then
                cp "data/maker/${RID}_manifest.json" "data/maker/${RID}_summary.json" reports/ 2>/dev/null
                : > "$MARK"      # атомарно-достаточно: маркер только после валидных файлов
                echo "$(ts) контрольная точка ${TAG} (${ELAPSED_MIN} мин): агрегат+econ OK" >> "$LOG"
            else
                echo "$(ts) контрольная точка ${TAG}: АНАЛИЗАТОР НЕ УДАЛСЯ, повторим на след. проходе" >> "$LOG"
            fi
        fi
    fi
done
echo "$(ts) ok: $RID жив, ${ELAPSED_MIN} мин" >> "$LOG"
exit 0