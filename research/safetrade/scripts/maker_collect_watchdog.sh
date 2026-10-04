#!/usr/bin/env bash
# Watchdog для 44-часового maker-сбора (PRL/QUANTUS/LTC, ревью d891422 + 4a3dc86).
# Запуск из cron: health раз в минуту, full каждые 30 мин:
#   * * * * *   .../maker_collect_watchdog.sh health
#   */30 * * *  .../maker_collect_watchdog.sh
#
# Ключевые правила (4a3dc86):
# - финальный/промежуточный отчёты генерируются и после завершения/остановки
#   run: стоп-здоровье применяется ТОЛЬКО к живому процессу; завершённый
#   коллектор получает отчёты независимо от свежести данных и STOP_MARK;
# - для фиксированных контрольных точек cutoff = ТОЧНОЕ start + H (не tail);
# - валидный стакан = valid_book (bids/asks не crossed, числовые);
# - любой rc checker != 0 -> ERROR/UNKNOWN, никогда «ok»;
# - PID/run привязка: проверка времени старта процесса против started_epoch,
#   сигнал только при подтверждённой принадлежности;
# - .done только после успешных анализаторов и валидного JSON (run_id+cutoff).
set -u
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
cd "$PROJECT_DIR" || exit 1
mkdir -p logs reports
exec 9>logs/maker_watchdog.lock
flock -n 9 || exit 0

MODE="${1:-full}"     # 'health' = только проверка живости/возраста (лёгкий)
VENV_BIN="$(readlink -f "$PROJECT_DIR/venv/bin/python" 2>/dev/null || echo "$PROJECT_DIR/venv/bin/python")"
LOG="logs/maker_watchdog.log"
ts() { date -u +%FT%TZ; }

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
START_EPOCH_FULL=$(grep -o '"started_epoch": *[0-9.]*' "$MANIFEST" | grep -o '[0-9.]*' | head -1)
[ -n "$START_EPOCH_FULL" ] || { echo "$(ts) ERROR: нет started_epoch в $MANIFEST" >> "$LOG"; exit 1; }
START_EPOCH="${START_EPOCH_FULL%.*}"   # целое — только для elapsed/привязки PID
NOW_EPOCH=$(date +%s)
ELAPSED_MIN=$(( (NOW_EPOCH - START_EPOCH) / 60 ))

# --- привязка PID: только python-процесс коллектора (не zsh-обёртка),
# стартовавший около started_epoch (допуск 600с) и с тем же data-dir.
# Команда не содержит run_id (генерится внутри), поэтому проверяем start time.
COLL_PROC=""
for pid in $(pgrep -f "venv/bin/python cli.py maker-collect --pairs PRLUSDT,QUANTUSUSDT,LTCUSDT" 2>/dev/null); do
    ps_start=$(ps -o lstart= -p "$pid" 2>/dev/null)
    ps_epoch=$(date -d "$ps_start" +%s 2>/dev/null) || continue
    if [ "$ps_epoch" -ge "$((START_EPOCH - 600))" ]; then
        COLL_PROC="$pid"
        break
    fi
done
[ -n "$COLL_PROC" ] && ALIVE=1 || ALIVE=0

# --- здоровье данных проверяется ВСЕГДА (не зависит от живости процесса):
# любой rc checker != 0 => ERROR, никогда «ok»; причина (stdout) при ЖИВОМ
# процессе => AUTO-STOP; при завершённом run причины свежести игнорируются
# (отчёты генерируются независимо — 4a3dc86 п.1), кроме явного ERROR.
HEALTH_OUT=$("$VENV_BIN" - "$RID" "$PROJECT_DIR" <<'PYEOF'
import json, os, sys, time
sys.path.insert(0, os.path.expanduser('~/safetrade-research'))
try:
    from maker.util import valid_book
except Exception:
    valid_book = None
rid, root = sys.argv[1], sys.argv[2]
mdir = os.path.join(root, 'data', 'maker')
try:
    manifest = json.load(open(os.path.join(mdir, f'{rid}_manifest.json')))
except Exception as e:
    print(f'ERROR manifest: {e}', flush=True); sys.exit(2)
pairs = manifest.get('pairs', [])
rows = []
tr_path = os.path.join(mdir, f'{rid}_trades.jsonl')
try:
    with open(tr_path) as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except ValueError:
                continue   # недописанный хвост файла допустим (flush на следующем тике)
            if not isinstance(obj, dict):
                print(f'ERROR bad_row: не-dict строка в trades: {ln[:60]}', flush=True)
                sys.exit(2)
            rows.append(obj)
except FileNotFoundError:
    pass
NONFULL = ('request_failed', 'history_truncated', 'coverage_unknown', 'pagination_not_advancing')
try:
    for pair in pairs:
        live = [r for r in rows if isinstance(r, dict) and r.get('pair') == pair
                and r.get('warmup') is False]
        if len(live) >= 3 and all(r.get('coverage') in NONFULL for r in live[-3:]):
            print(f'3_notfull_live_{pair}', flush=True); sys.exit(0)
    # per-pair depth age: ВАЛИДНЫЙ стакан (valid_book: не crossed, числовые)
    now = time.time()
    per_pair_age = {}
    dp_path = os.path.join(mdir, f'{rid}_depth.jsonl')
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
                if valid_book is not None:
                    okb, _, _ = valid_book(b, a)
                else:
                    okb = bool(b and a)
                if not okb:
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
except Exception as e:
    print(f'ERROR checker: {type(e).__name__}: {e}', flush=True); sys.exit(2)
sys.exit(0)
PYEOF
)
HEALTH_RC=$?
if [ "$HEALTH_RC" -ne 0 ]; then
    echo "$(ts) ERROR health-checker (rc=$HEALTH_RC): $HEALTH_OUT" >&2
    echo "$(ts) ERROR health-checker (rc=$HEALTH_RC): $HEALTH_OUT" >> "$LOG"
    exit 1                       # не «ok»: checker failure НЕ здоровье
fi
if [ "$ALIVE" = "1" ]; then
    [ ! -f "$STOP_MARK" ] || exit 0   # stop-state конкретного run: только при живом
    if [ -n "$HEALTH_OUT" ]; then
        echo "$(ts) AUTO-STOP: $HEALTH_OUT у $RID (pid=$COLL_PROC)" >> "$LOG"
        touch "$STOP_MARK"
        kill "$COLL_PROC" 2>/dev/null   # SIGTERM: finally коллектора допишет summary
        exit 0
    fi
    [ "$MODE" = "health" ] && exit 0
else
    if [ -n "$HEALTH_OUT" ]; then
        # завершённый run: свежесть depth не критерий, отчёты всё равно нужны
        echo "$(ts) WARN: $HEALTH_OUT (run завершён, отчёты продолжаем)" >> "$LOG"
    fi
    [ "$MODE" = "health" ] && exit 0
fi

# --- контрольные точки: ТОЧНЫЙ cutoff = start + H*60с (4a3dc86 п.4);
# .done ТОЛЬКО при успехе; работает и после завершения процесса.
for PTS in 120:2h 360:6h 1440:24h 2640:44h; do
    LIMIT_MIN="${PTS%%:*}"
    TAG="${PTS##*:}"
    MARK="reports/${RID}_${TAG}.done"
    if [ "$ELAPSED_MIN" -ge "$LIMIT_MIN" ] && [ ! -f "$MARK" ]; then
        # точный cutoff: started_epoch + LIMIT_MIN минут (в наносекундах,
        # Decimal для сохранения дробной доли секунды — 4a3dc86 п.4)
        CUTOFF=$("$VENV_BIN" -c "
from decimal import Decimal
print(int(Decimal('$START_EPOCH_FULL')*1_000_000_000 + Decimal($LIMIT_MIN*60)*1_000_000_000))
" 2>/dev/null)
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
            if [ "$OK" = 1 ] && "$VENV_BIN" -c "
import json
p='reports/econ_${RID}_${TAG}.json'
d=json.load(open(p))
assert d.get('run_id')=='${RID}', d.get('run_id')
assert d.get('cutoff_ns')==int('$CUTOFF'), (d.get('cutoff_ns'), int('$CUTOFF'))
" >>"$LOG" 2>&1; then
                cp "data/maker/${RID}_manifest.json" "data/maker/${RID}_summary.json" reports/ 2>/dev/null
                : > "$MARK"
                echo "$(ts) контрольная точка ${TAG}: cutoff $CUTOFF OK" >> "$LOG"
            else
                echo "$(ts) контрольная точка ${TAG}: АНАЛИЗАТОР НЕ УДАЛСЯ, повторим на след. проходе" >> "$LOG"
            fi
        fi
    fi
done
if [ "$ALIVE" = "1" ]; then
    echo "$(ts) ok: $RID жив, ${ELAPSED_MIN} мин" >> "$LOG"
else
    echo "$(ts) ok: $RID завершён/не запущен (process absent), ${ELAPSED_MIN} мин, отчёты проверены" >> "$LOG"
fi
exit 0