"""光功率数据 SQLite 存储层。

- files 表：每个扫过的 xlsx 记一条（去重/增量：知道哪些文件处理过）
- records 表：每条记录一行，UNIQUE(source_file, sheet, row) -> 重复就覆盖
- 索引：box_name / source_file / 置信度 / 主要问题 / (lat,lon)
- 随时导出 CSV（可按条件筛）

用法：
    python pt_db.py init  --db pt_data.sqlite
    python pt_db.py store --db pt_data.sqlite --rows pt_all/rows.json --ocr pt_all/ocr \
                          --model gemini-3.5-flash --mode refresh
    python pt_db.py export --db pt_data.sqlite --out master.csv [--where "置信度='高'"]
    python pt_db.py files  --db pt_data.sqlite        # 看已处理的文件

模式(处理已入库的文件时)：refresh=重跑覆盖 / skip=跳过 / ask=询问
"""

import argparse
import csv
import json
import os
import sqlite3
import sys
import time

from pt_merge import reconcile, load_ocr, suffix
import pt_rules

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_COLS = [
    "source_file", "sheet", "row", "cluster", "box_name", "power_dbm",
    "lat", "lon", "address", "addr_area", "addr_estate", "addr_street", "test_date", "pass_fail",
    "conf", "photo_status", "power_check", "box_check",
    "has_coord", "has_addr", "fixable_coord", "main_issue",
    "box_image", "power_image_dbm", "model", "ocr_at",
]
NEW_ADDR_COLS = ["addr_area", "addr_estate", "addr_street"]   # 旧库迁移要补的地址列
REC_COLS = BASE_COLS + pt_rules.DERIVED_COLS   # 追加派生列(层级/合格/复测)

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    file_name    TEXT PRIMARY KEY,
    file_size    INTEGER,
    sheets       INTEGER,
    rows         INTEGER,
    photos       INTEGER,
    record_count INTEGER,
    model        TEXT,
    processed_at TEXT
);
CREATE TABLE IF NOT EXISTS records (
    id           INTEGER PRIMARY KEY,
    source_file  TEXT, sheet TEXT, row INTEGER,
    cluster TEXT, box_name TEXT, power_dbm REAL,
    lat REAL, lon REAL, address TEXT, addr_area TEXT, addr_estate TEXT, addr_street TEXT,
    test_date TEXT, pass_fail TEXT,
    conf TEXT, photo_status TEXT, power_check TEXT, box_check TEXT,
    has_coord TEXT, has_addr TEXT, fixable_coord TEXT, main_issue TEXT,
    box_image TEXT, power_image_dbm REAL, model TEXT, ocr_at TEXT,
    area TEXT, cluster_code TEXT, zone TEXT, hub TEXT, level TEXT, fat TEXT,
    fat_valid TEXT, power_status_ TEXT, needs_retest TEXT, retest_reason TEXT, retest_priority TEXT,
    UNIQUE(source_file, sheet, row)
);
CREATE INDEX IF NOT EXISTS idx_rec_box      ON records(box_name);
CREATE INDEX IF NOT EXISTS idx_rec_file     ON records(source_file);
CREATE INDEX IF NOT EXISTS idx_rec_conf     ON records(conf);
CREATE INDEX IF NOT EXISTS idx_rec_issue    ON records(main_issue);
CREATE INDEX IF NOT EXISTS idx_rec_lonlat   ON records(lon, lat);
"""

# 派生列上的索引（在补列之后再建）
DERIVED_INDEX = """
CREATE INDEX IF NOT EXISTS idx_rec_area   ON records(area);
CREATE INDEX IF NOT EXISTS idx_rec_retest ON records(needs_retest, retest_priority);
CREATE INDEX IF NOT EXISTS idx_rec_pstat  ON records(power_status_);
"""


def connect(db):
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    # 迁移旧库：补上派生列和地址三列（在建派生列索引之前）
    have = {r[1] for r in conn.execute("PRAGMA table_info(records)")}
    for col in pt_rules.DERIVED_COLS + NEW_ADDR_COLS:
        if col not in have:
            conn.execute(f"ALTER TABLE records ADD COLUMN {col} TEXT")
    conn.executescript(DERIVED_INDEX)
    conn.commit()
    return conn


def _to_float(x):
    try:
        return float(str(x).replace(",", "").strip())
    except (ValueError, AttributeError, TypeError):
        return None


def build_records(rows, ocr_dir, model, ts):
    """把 rows.json + ocr 目录 reconcile 成 record 字典列表（列同 REC_COLS）。"""
    ocr = load_ocr(ocr_dir)
    out = []
    for r in rows:
        if not suffix(r.get("box_table")):
            continue
        o = ocr.get((r.get("source_file"), r.get("sheet"), r["row"]), {})
        rec = reconcile(r, o)
        power = _to_float(rec["power"])
        base = {
            "source_file": r.get("source_file"), "sheet": r.get("sheet"), "row": r["row"],
            "cluster": r.get("cluster"), "box_name": rec["box"], "power_dbm": power,
            "lat": _to_float(rec["lat"]), "lon": _to_float(rec["lon"]), "address": rec["addr"],
            "addr_area": rec["addr_area"], "addr_estate": rec["addr_estate"], "addr_street": rec["addr_street"],
            "test_date": o.get("timestamp") or r.get("date_table"), "pass_fail": r.get("passfail_table"),
            "conf": rec["置信度"], "photo_status": rec["照片状态"], "power_check": rec["功率核对"],
            "box_check": rec["盒子核对"], "has_coord": rec["有坐标"], "has_addr": rec["有地址"],
            "fixable_coord": rec["可修坐标"], "main_issue": rec["主要问题"],
            "box_image": o.get("box_name_image"), "power_image_dbm": _to_float(o.get("power_dbm")),
            "model": model, "ocr_at": ts,
        }
        base.update(pt_rules.derive(rec["box"], power, rec["主要问题"], rec["有坐标"], rec["照片状态"],
                                    r.get("cluster"), r.get("sheet"), r.get("source_file")))
        out.append(base)
    return out


def enrich(conn):
    """对库里已有记录，用 pt_rules 现算派生列(层级/合格/复测)并回写。无需重跑 OCR。"""
    rows = conn.execute("SELECT id, box_name, power_dbm, main_issue, has_coord, photo_status, cluster, sheet, source_file FROM records").fetchall()
    n = 0
    for rid, box, power, issue, has_coord, photo, cluster, sheet, src in rows:
        d = pt_rules.derive(box, power, issue, has_coord, photo, cluster, sheet, src)
        sets = ", ".join(f"{k}=?" for k in pt_rules.DERIVED_COLS)
        conn.execute(f"UPDATE records SET {sets} WHERE id=?", [d[k] for k in pt_rules.DERIVED_COLS] + [rid])
        n += 1
    conn.commit()
    print(f"已重算派生列: {n} 条")


def upsert_records(conn, records):
    ph = ",".join("?" * len(REC_COLS))
    upd = ",".join(f"{c}=excluded.{c}" for c in REC_COLS if c not in ("source_file", "sheet", "row"))
    sql = (f"INSERT INTO records ({','.join(REC_COLS)}) VALUES ({ph}) "
           f"ON CONFLICT(source_file, sheet, row) DO UPDATE SET {upd}")
    conn.executemany(sql, [[rec.get(c) for c in REC_COLS] for rec in records])


def upsert_file(conn, meta):
    cols = ["file_name", "file_size", "sheets", "rows", "photos", "record_count", "model", "processed_at"]
    ph = ",".join("?" * len(cols))
    upd = ",".join(f"{c}=excluded.{c}" for c in cols if c != "file_name")
    conn.execute(f"INSERT INTO files ({','.join(cols)}) VALUES ({ph}) "
                 f"ON CONFLICT(file_name) DO UPDATE SET {upd}", [meta.get(c) for c in cols])


def file_done(conn, name):
    return conn.execute("SELECT 1 FROM files WHERE file_name=?", (name,)).fetchone() is not None


def store(conn, rows_json, ocr_dir, model, mode="refresh"):
    rows = json.load(open(rows_json, encoding="utf-8"))
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    by_file = {}
    for r in rows:
        by_file.setdefault(r.get("source_file"), []).append(r)

    for fname, frows in by_file.items():
        if file_done(conn, fname):
            if mode == "skip":
                print(f"  [跳过] {fname}（已入库）"); continue
            if mode == "ask":
                ans = input(f"  {fname} 已入库，覆盖? [y/N] ").strip().lower()
                if ans != "y":
                    print("  跳过"); continue
        recs = build_records(frows, ocr_dir, model, ts)
        conn.execute("DELETE FROM records WHERE source_file=?", (fname,))  # 覆盖：先清该文件旧记录
        upsert_records(conn, recs)
        upsert_file(conn, {
            "file_name": fname, "file_size": None,
            "sheets": len({r.get("sheet") for r in frows}),
            "rows": len(frows), "photos": sum(1 for r in frows if r.get("has_photo")),
            "record_count": len(recs), "model": model, "processed_at": ts,
        })
        conn.commit()
        print(f"  [入库] {fname}: {len(recs)} 条记录")


def export(conn, out, where=None):
    sql = "SELECT " + ",".join(REC_COLS) + " FROM records"
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY source_file, sheet, row"
    cur = conn.execute(sql)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(REC_COLS)
        n = 0
        for r in cur:
            w.writerow(r); n += 1
    print(f"导出 {n} 行 -> {out}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "store", "export", "files", "enrich"):
        p = sub.add_parser(name)
        p.add_argument("--db", default="pt_data.sqlite")
        if name == "store":
            p.add_argument("--rows", required=True)
            p.add_argument("--ocr", required=True)
            p.add_argument("--model", default="gemini-3.5-flash")
            p.add_argument("--mode", default="refresh", choices=["refresh", "skip", "ask"])
        if name == "export":
            p.add_argument("--out", required=True)
            p.add_argument("--where", default=None, help="SQL 条件，如 \"置信度='高'\" 注意用列英文名 conf")
    args = ap.parse_args(argv)

    conn = connect(args.db)
    if args.cmd == "init":
        print(f"库已就绪: {args.db}")
    elif args.cmd == "store":
        store(conn, args.rows, args.ocr, args.model, args.mode)
    elif args.cmd == "export":
        export(conn, args.out, args.where)
    elif args.cmd == "enrich":
        enrich(conn)
    elif args.cmd == "files":
        for r in conn.execute("SELECT file_name, record_count, photos, model, processed_at FROM files ORDER BY processed_at"):
            print(f"  {r[0]}  记录{r[1]} 图{r[2]}  {r[3]}  {r[4]}")
    conn.close()


if __name__ == "__main__":
    main()
