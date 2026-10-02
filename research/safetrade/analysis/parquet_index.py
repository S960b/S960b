"""Manifest/index parquet-файлов (ревью P1.4): выбор последнего run по данным,
а не по времени записи имени файла. Сканирует только мета-колонки."""
import json
import os

import pyarrow.dataset as ds


def build_parquet_index(parquet_dir: str) -> dict:
    """Один проход по parquet: run_id/boot_id → min/max recv_utc_ns, число событий.
    Возвращает dict: {'runs': [{run_id, boot_id, start_utc_ns, end_utc_ns, n}], 'files': n}."""
    files = [os.path.join(root, f) for root, _, fs in os.walk(parquet_dir)
             for f in fs if f.endswith(".parquet") and not f.startswith(".")]
    if not files:
        return {"runs": [], "files": 0}
    d = ds.dataset(files, format="parquet")
    tbl = d.to_table(columns=["run_id", "boot_id", "recv_utc_ns"])
    import pandas as pd
    meta = tbl.to_pandas()
    if meta.empty:
        return {"runs": [], "files": len(files)}
    g = meta.groupby(["run_id", "boot_id"])["recv_utc_ns"].agg(["min", "max", "count"]).reset_index()
    runs = []
    for _, r in g.iterrows():
        runs.append({"run_id": str(r["run_id"]), "boot_id": str(r["boot_id"]),
                     "start_utc_ns": int(r["min"]), "end_utc_ns": int(r["max"]),
                     "n_events": int(r["count"])})
    runs.sort(key=lambda r: r["start_utc_ns"])
    return {"runs": runs, "files": len(files)}


def save_parquet_index(parquet_dir: str, reports_dir: str) -> dict:
    idx = build_parquet_index(parquet_dir)
    os.makedirs(reports_dir, exist_ok=True)
    with open(os.path.join(reports_dir, "parquet_index.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, indent=1, ensure_ascii=False)
    return idx


def latest_run_from_index(reports_dir: str):
    """run_id/boot_id последнего run по индексу (если он есть), иначе None."""
    path = os.path.join(reports_dir, "parquet_index.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        idx = json.load(f)
    if not idx.get("runs"):
        return None
    last = idx["runs"][-1]
    return last["run_id"], last["boot_id"]