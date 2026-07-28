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
