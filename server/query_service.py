"""光功率工具箱 V2.0 查询业务层：records 级合并去重 SQL + 分页/单条查询。

职责边界：本模块只做「合并去重 SQL 构造 + 参数化查询执行」，不碰 HTTP。
路由解析/响应组装在 server/routes_query.py，DB 连接建表在 server/db.py。

records 级合并（vs scripts/pt_report.py 盒子级 BOXES_SQL 的三处差异）
------------------------------------------------------------------
① 合并键 fallback L1→L3→孤儿：
   - L1（area/hub/level/fat 全非 NULL）：area||COALESCE(cluster_code,'')||
     COALESCE(zone,'')||hub||level||fat，与 pt_report box_key 一致。
   - L3（L1 不全）：box_name 清洗后非空 → 'BN:'||UPPER(TRIM(box_name))，
     加 'BN:' 前缀防与 L1 拼接串意外撞车。
   - 孤儿（L1 不全且 box_name 清洗后空）：merge_key = NULL。
② evidence 放宽：photo_status='有图清晰' 即 evidence=1（去掉 pt_report 的
   AND power_check IN(...)）。conf_rank 同 pt_report（高0中1低2无效3其他4）。
③ 默认排除：duplicate_of 非空（重复副本，来自 files.duplicate_of）默认不返回，
   include_duplicates 才含；孤儿（merge_key IS NULL）默认不返回，include_orphans 才含。

赢家选取：ROW_NUMBER() OVER (PARTITION BY merge_key
          ORDER BY evidence DESC, conf_rank ASC, ocr_at DESC) 取 rn=1。
顺序：先合并选赢家 → 应用筛选 → count total → LIMIT/OFFSET（先合并再分页）。
"""

import sqlite3
from typing import Any

# records 表全列（含自增主键 id），返回赢家记录时取全字段。
# 与 db.py SCHEMA 的 records 列定义对齐。
RECORD_COLS: list[str] = [
    "id", "source_file", "sheet", "row",
    "cluster", "box_name", "power_dbm",
    "lat", "lon", "address", "addr_area", "addr_estate", "addr_street",
    "test_date", "pass_fail", "conf", "photo_status", "power_check", "box_check",
    "has_coord", "has_addr", "fixable_coord", "main_issue",
    "box_image", "power_image_dbm", "model", "ocr_at",
    "area", "cluster_code", "zone", "hub", "level", "fat",
    "fat_valid", "power_status_", "needs_retest", "retest_reason", "retest_priority",
    "image_path",
]

# 可作筛选的字段白名单 → records 表列名（防注入：键固定，只有值走参数化）。
FILTER_COLS: dict[str, str] = {
    "area": "area",
    "zone": "zone",
    "hub": "hub",
    "box_name": "box_name",
    "needs_retest": "needs_retest",
    "photo_status": "photo_status",
    "conf": "conf",
}

# 合并 CTE：为每条 records 计算 merge_key / evidence / conf_rank / is_duplicate，
# 再用窗口函数在同 merge_key 内选赢家（rn=1）。r.* 保留 records 全字段。
# is_duplicate 来自 files.duplicate_of（记录所属源文件是否为重复副本）。
_MERGE_CTE = """
WITH tagged AS (
    SELECT
        r.*,
        CASE
            WHEN r.area IS NOT NULL AND r.hub IS NOT NULL
                 AND r.level IS NOT NULL AND r.fat IS NOT NULL
            THEN r.area || COALESCE(r.cluster_code,'') || COALESCE(r.zone,'')
                 || r.hub || r.level || r.fat
            WHEN TRIM(COALESCE(r.box_name,'')) <> ''
            THEN 'BN:' || UPPER(TRIM(r.box_name))
            ELSE NULL
        END AS merge_key,
        CASE WHEN r.photo_status = '有图清晰' THEN 1 ELSE 0 END AS evidence,
        CASE r.conf WHEN '高' THEN 0 WHEN '中' THEN 1 WHEN '低' THEN 2
             WHEN '无效' THEN 3 ELSE 4 END AS conf_rank,
        CASE WHEN f.duplicate_of IS NOT NULL THEN 1 ELSE 0 END AS is_duplicate
    FROM records r
    LEFT JOIN files f ON f.file_name = r.source_file
),
winners AS (
    SELECT * FROM (
        SELECT t.*,
               ROW_NUMBER() OVER (
                   PARTITION BY merge_key
                   ORDER BY evidence DESC, conf_rank ASC, ocr_at DESC
               ) AS rn
        FROM tagged t
    ) WHERE rn = 1
)
"""


def _build_filters(
    filters: dict[str, Any],
    include_duplicates: bool,
    include_orphans: bool,
) -> tuple[str, list[Any]]:
    """构造作用在赢家记录上的 WHERE 子句 + 参数列表（全部参数化）。

    - 字段筛选：仅取 FILTER_COLS 白名单键，值走 ? 占位。
    - include_duplicates=False → 排除 is_duplicate=1（重复副本）。
    - include_orphans=False → 排除 merge_key IS NULL（孤儿）。
    这两个开关控制 WHERE 结构分支，不注入值。
    """
    clauses: list[str] = []
    params: list[Any] = []
    for key, col in FILTER_COLS.items():
        val = filters.get(key)
        if val is not None:
            clauses.append(f"{col} = ?")
            params.append(val)
    if not include_duplicates:
        clauses.append("is_duplicate = 0")
    if not include_orphans:
        clauses.append("merge_key IS NOT NULL")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def query_records(
    conn: sqlite3.Connection,
    filters: dict[str, Any],
    page: int = 1,
    page_size: int = 50,
    include_duplicates: bool = False,
    include_orphans: bool = False,
) -> dict[str, Any]:
    """合并去重后分页查询，返回 {total, page, page_size, records:[赢家全字段]}。

    先合并选赢家 → 应用筛选 → count total → LIMIT/OFFSET（先合并再分页）。
    SQL 全参数化。page/page_size 下限保护为 1。
    """
    page = max(1, page)
    page_size = max(1, page_size)
    where, params = _build_filters(filters, include_duplicates, include_orphans)

    total = conn.execute(
        f"{_MERGE_CTE} SELECT COUNT(*) FROM winners{where}", params
    ).fetchone()[0]

    col_list = ", ".join(RECORD_COLS)
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"{_MERGE_CTE} SELECT {col_list} FROM winners{where} "
        f"ORDER BY id LIMIT ? OFFSET ?",
        [*params, page_size, offset],
    ).fetchall()

    records = [dict(zip(RECORD_COLS, r)) for r in rows]
    return {"total": total, "page": page, "page_size": page_size, "records": records}


def get_record_by_id(conn: sqlite3.Connection, record_id: int) -> dict[str, Any] | None:
    """按 records.id 取单条全字段 dict，不存在返回 None。SQL 参数化。"""
    col_list = ", ".join(RECORD_COLS)
    row = conn.execute(
        f"SELECT {col_list} FROM records WHERE id = ?", (record_id,)
    ).fetchone()
    return dict(zip(RECORD_COLS, row)) if row else None


def get_image_path_by_id(conn: sqlite3.Connection, record_id: int) -> str | None:
    """按 records.id 取 image_path（原图绝对路径），空/NULL/不存在均返回 None。"""
    row = conn.execute(
        "SELECT image_path FROM records WHERE id = ?", (record_id,)
    ).fetchone()
    if not row or not row[0]:
        return None
    return row[0]
