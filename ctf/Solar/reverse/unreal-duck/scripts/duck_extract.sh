#!/usr/bin/env bash
# UE5 IoStore CTF extraction driver. Usage: ./duck_extract.sh /path/to/UnrealDuck/Content/Paks [output_dir]
set -uo pipefail
PAKS="${1:?Usage: $0 /path/to/Content/Paks [output_dir]}"
OUT="${2:-$PWD/duck_analysis}"
PAKS="$(realpath "$PAKS")"
mkdir -p "$OUT"/reports "$OUT"/raw_pak "$OUT"/legacy_assets
OUT="$(realpath "$OUT")"
[ -d "$PAKS" ] || { echo "Not a directory: $PAKS" >&2; exit 2; }
command -v retoc >/dev/null || { echo 'Install retoc: https://github.com/trumank/retoc/releases' >&2; exit 2; }
command -v repak >/dev/null || { echo 'Install repak: https://github.com/trumank/repak/releases' >&2; exit 2; }
echo "Paks: $PAKS; out: $OUT"
find "$PAKS" -maxdepth 1 -type f \( -iname '*.pak' -o -iname '*.ucas' -o -iname '*.utoc' \) -printf '%f\t%s bytes\n' | sort > "$OUT/reports/containers.txt"
# For analysis use small known initial metadata and content names before extraction.
shopt -s nullglob nocaseglob
for toc in "$PAKS"/*.utoc; do
  base="$(basename "$toc" .utoc)"
  echo "[retoc] $base"
  retoc info "$toc" > "$OUT/reports/${base}.info.txt" 2>&1 || true
  retoc list "$toc" > "$OUT/reports/${base}.list.txt" 2>&1 || true
done
for pak in "$PAKS"/*.pak; do
  base="$(basename "$pak" .pak)"
  echo "[repak] $base"
  repak info "$pak" > "$OUT/reports/${base}.pak-info.txt" 2>&1 || true
  repak list "$pak" > "$OUT/reports/${base}.pak-list.txt" 2>&1 || true
  mkdir -p "$OUT/raw_pak/$base"
  repak unpack "$pak" -o "$OUT/raw_pak/$base" > "$OUT/reports/${base}.pak-unpack.log" 2>&1 || echo "WARNING: repak failed for $pak; see log" >&2
done
# retoc TO-LEGACY parses the complete game Paks folder, including global.utoc.
echo '[retoc] converting IoStore -> Legacy .pak'
if retoc to-legacy "$PAKS" "$OUT/legacy_P.pak" > "$OUT/reports/retoc-to-legacy.log" 2>&1; then
  echo '[repak] unpacking legacy_P.pak'
  repak unpack "$OUT/legacy_P.pak" -o "$OUT/legacy_assets" > "$OUT/reports/repak-legacy-unpack.log" 2>&1 || echo 'WARNING: repak unpack failed; check log' >&2
else
  echo 'WARNING: retoc conversion failed; inspect reports/retoc-to-legacy.log' >&2
fi
find "$OUT/raw_pak" "$OUT/legacy_assets" -type f | sort > "$OUT/reports/all_files.txt"
find "$OUT/raw_pak" "$OUT/legacy_assets" -type f | grep -Ei '(flag|secret|duck|level|map|widget|hud|game|blueprint|text|locale|string|assetregistry|\.umap|\.locres|\.ini|\.json|\.txt)' > "$OUT/reports/priority_files.txt" || true
if [ -f "$(dirname "$0")/duck_scan.py" ]; then
  python3 "$(dirname "$0")/duck_scan.py" "$OUT/raw_pak" "$OUT/legacy_assets" | tee "$OUT/reports/strings_hits.txt"
else
  echo 'Place duck_scan.py beside duck_extract.sh to scan binary ASCII/UTF-16LE strings.'
fi
echo "Reports are in $OUT/reports; extracted files in $OUT/raw_pak and $OUT/legacy_assets"
