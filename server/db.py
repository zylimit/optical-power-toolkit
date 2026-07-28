"""光功率工具箱 V2.0 服务端 SQLite 存储层。

独立重建 schema 与迁移（不 import scripts/pt_db.py，避免服务端依赖客户端代码）。
- records 表：沿用 V1 records 全字段（base + 派生 + 地址三列）+ 新增 image_path
- files 表：沿用 V1 files 字段 + 新增 content_md5 / file_size / duplicate_of
- connect()：建表 + PRAGMA table_info 幂等迁移补列，旧库打开不报错

注：file_size 在 V1 files 表已存在；V2 迁移列表仍列入，PRAGMA 检查命中即跳过（幂等）。
"""

import sqlite3
import sys
from typing import List

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# records 基础列（与 scripts/pt_db.py BASE_COLS 对齐）
BASE_COLS: List[str] = [
    "source_file", "sheet", "row", "cluster", "box_name", "power_dbm",
    "lat", "lon", "address", "addr_area", "addr_estate", "addr_street",
    "test_date", "pass_fail", "conf", "photo_status", "power_check", "box_check",
    "has_coord", "has_addr", "fixable_coord", "main_issue",
    "box_image", "power_image_dbm", "model", "ocr_at",
]
# 派生列（层级/合格/复测，与 scripts/pt_rules.py DERIVED_COLS 对齐）
DERIVED_COLS: List[str] = [
    "area", "cluster_code", "zone", "hub", "level", "fat", "fat_valid",
    "power_status_", "needs_retest", "retest_reason", "retest_priority",
]
# 极旧 V1 库可能缺的地址三列（防御性迁移）
NEW_ADDR_COLS: List[str] = ["addr_area", "addr_estate", "addr_street"]
# records 全列（不含 V2 新增 image_path，image_path 单独迁移）
REC_COLS: List[str] = BASE_COLS + DERIVED_COLS

# files 表 V2 新增列（name + DDL 类型，ALTER 时原样拼接）
FILES_NEW_COLS: List[str] = ["content_md5 TEXT", "file_size INTEGER", "duplicate_of TEXT"]
# records 表 V2 新增列
REC_NEW_COLS: List[str] = ["image_path TEXT"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    file_name    TEXT PRIMARY KEY,
    file_size    INTEGER,
    sheets       INTEGER,
    rows         INTEGER,
    photos       INTEGER,
    record_count INTEGER,
    model        TEXT,
    processed_at TEXT,
    content_md5  TEXT,
    duplicate_of TEXT
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
    image_path TEXT,
    UNIQUE(source_file, sheet, row)
);
CREATE INDEX IF NOT EXISTS idx_rec_box      ON records(box_name);
CREATE INDEX IF NOT EXISTS idx_rec_file     ON records(source_file);
CREATE INDEX IF NOT EXISTS idx_rec_conf     ON records(conf);
CREATE INDEX IF NOT EXISTS idx_rec_issue    ON records(main_issue);
CREATE INDEX IF NOT EXISTS idx_rec_lonlat   ON records(lon, lat);
"""

# 派生列上的索引（补列之后再建，沿用 pt_db.py 模式）
DERIVED_INDEX = """
CREATE INDEX IF NOT EXISTS idx_rec_area   ON records(area);
CREATE INDEX IF NOT EXISTS idx_rec_retest ON records(needs_retest, retest_priority);
CREATE INDEX IF NOT EXISTS idx_rec_pstat  ON records(power_status_);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """连接 DB，建表，幂等迁移补齐新列，返回 conn。

    旧库（V1，缺 V2 新列或派生/地址列）打开不报错，PRAGMA table_info 检查后 ALTER 补齐。
    迁移幂等：已存在的列不重复 ALTER。
    """
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    # records 迁移：补派生列 + 地址三列（极旧 V1）+ image_path（V2 新增）
    rec_have = {row[1] for row in conn.execute("PRAGMA table_info(records)")}
    for col in DERIVED_COLS + NEW_ADDR_COLS + ["image_path"]:
        if col not in rec_have:
            conn.execute(f"ALTER TABLE records ADD COLUMN {col} TEXT")

    # files 迁移：补 content_md5 / file_size / duplicate_of（V2 新增；file_size 在 V1 已有则跳过）
    files_have = {row[1] for row in conn.execute("PRAGMA table_info(files)")}
    for col_def in FILES_NEW_COLS:
        name = col_def.split()[0]
        if name not in files_have:
            conn.execute(f"ALTER TABLE files ADD COLUMN {col_def}")

    conn.executescript(DERIVED_INDEX)
    conn.commit()
    return conn


# records 可写列白名单（全列 + image_path，不含自增主键 id）。
# 上传入库时仅取 record dict 中命中此白名单的键，未知键静默忽略（防客户端脏字段污染 SQL）。
WRITABLE_REC_COLS: List[str] = REC_COLS + ["image_path"]

# files 表可写列（record_count 由服务端统计，processed_at 服务端打时间戳）。
FILES_COLS: List[str] = [
    "file_name", "file_size", "sheets", "rows", "photos",
    "record_count", "model", "processed_at", "content_md5", "duplicate_of",
]


def upsert_record(conn: sqlite3.Connection, source_file: str, record: dict) -> None:
    """按唯一键 (source_file, sheet, row) upsert 一条 records。

    record 为客户端提交的字段字典，仅取命中 WRITABLE_REC_COLS 的键（未知键忽略）。
    source_file 以路径参数为准，覆盖 record 里可能带的同名字段。
    冲突（同 source_file+sheet+row 已存在）时按新值更新全部写入列（幂等覆盖）。
    SQL 参数化，不拼接。
    """
    data = {k: record[k] for k in WRITABLE_REC_COLS if k in record}
    data["source_file"] = source_file  # 路径参数权威

    cols = list(data.keys())
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    # 冲突列（唯一键）不参与 SET，其余列全部覆盖
    conflict_keys = {"source_file", "sheet", "row"}
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in conflict_keys)

    sql = (
        f"INSERT INTO records ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT(source_file, sheet, row) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [data[c] for c in cols])


def get_file_by_md5_size(
    conn: sqlite3.Connection, content_md5: str, file_size: int
) -> str | None:
    """查 files 表有无同 content_md5+file_size 的文件，返回其 file_name（source_file），无则 None。

    只取非重复项（duplicate_of IS NULL）作复用源，避免链式指向重复项。
    """
    row = conn.execute(
        "SELECT file_name FROM files WHERE content_md5=? AND file_size=? "
        "AND duplicate_of IS NULL ORDER BY processed_at LIMIT 1",
        (content_md5, file_size),
    ).fetchone()
    return row[0] if row else None


def upsert_file(conn: sqlite3.Connection, file_row: dict) -> None:
    """按主键 file_name upsert 一条 files。仅取 FILES_COLS 命中键，SQL 参数化。"""
    data = {k: file_row[k] for k in FILES_COLS if k in file_row}
    cols = list(data.keys())
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "file_name")
    sql = (
        f"INSERT INTO files ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT(file_name) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [data[c] for c in cols])


def list_files(conn: sqlite3.Connection, since: str | None = None) -> List[dict]:
    """返回文件清单 [{source_file, content_md5, file_size, record_count, duplicate_of, ocr_at}]。

    ocr_at 取该文件 records 里最新 ocr_at，缺则回退 files.processed_at。
    since 非空时过滤 processed_at > since（字符串比较；ISO / unix-ts 串按字典序，
    调用方保证格式一致即可）。
    """
    sql = (
        "SELECT f.file_name, f.content_md5, f.file_size, f.record_count, "
        "f.duplicate_of, "
        "COALESCE((SELECT MAX(r.ocr_at) FROM records r WHERE r.source_file=f.file_name), "
        "f.processed_at) AS ocr_at "
        "FROM files f"
    )
    params: list = []
    if since:
        sql += " WHERE f.processed_at > ?"
        params.append(since)
    sql += " ORDER BY f.processed_at"
    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "source_file": r[0],
            "content_md5": r[1],
            "file_size": r[2],
            "record_count": r[3],
            "duplicate_of": r[4],
            "ocr_at": r[5],
        }
        for r in rows
    ]
