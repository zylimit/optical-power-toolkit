"""光功率盒子级去重报表。

records 表一行=一次测试，同一物理盒子会被多次记录。本工具按盒子身份归一
（box_key = area+cluster_code+zone+hub+level+fat），一盒一行，每盒选最佳记录：
有验证功率证据(evidence) > 置信度高 > OCR 时间新。

用法：
    python pt_report.py boxes --db pt_data.sqlite     # 打印盒子级 summary
"""

import argparse
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 归一范围：四段层级齐全才算可归一，缺任一段的记录归为 unparsed
PARSED_WHERE = "area IS NOT NULL AND hub IS NOT NULL AND level IS NOT NULL AND fat IS NOT NULL"

# 盒子级查询：parsed 抽取可归一记录并算 evidence/conf_rank ->
# agg 按盒聚合 -> best 窗口函数选每盒最佳记录 -> 拼盒子级复测判定
BOXES_SQL = f"""
WITH parsed AS (
    SELECT
        area || COALESCE(cluster_code,'') || COALESCE(zone,'') || hub || level || fat AS box_key,
        area, cluster_code, zone, hub, level, fat,
        source_file, sheet, row, box_name, power_dbm, lat, lon, address,
        test_date, ocr_at, conf, photo_status, power_check, power_status_, main_issue,
        CASE WHEN photo_status = '有图清晰' AND power_check IN ('一致','表缺已恢复')
             THEN 1 ELSE 0 END AS evidence,
        CASE conf WHEN '高' THEN 0 WHEN '中' THEN 1 WHEN '低' THEN 2 WHEN '无效' THEN 3
             ELSE 4 END AS conf_rank
    FROM records
    WHERE {PARSED_WHERE}
),
agg AS (
    SELECT box_key,
           COUNT(*)                     AS records_total,
           COUNT(DISTINCT source_file)  AS files_seen,
           MAX(evidence)                AS evid_max
    FROM parsed
    GROUP BY box_key
),
best AS (
    SELECT * FROM (
        SELECT p.*,
               ROW_NUMBER() OVER (PARTITION BY box_key
                                  ORDER BY evidence DESC, conf_rank ASC, ocr_at DESC) AS rn
        FROM parsed p
    ) WHERE rn = 1
)
SELECT
    b.box_key, b.area, b.cluster_code, b.zone, b.hub, b.level, b.fat,
    b.source_file, b.sheet, b.row, b.box_name, b.power_dbm, b.lat, b.lon, b.address,
    b.test_date, b.ocr_at, b.conf, b.photo_status, b.power_check, b.power_status_, b.main_issue,
    a.records_total, a.files_seen,
    CASE WHEN a.evid_max = 1 THEN '是' ELSE '否' END AS has_evidence,
    CASE WHEN a.evid_max = 0 THEN '是'
         WHEN b.power_status_ IN ('偏弱','偏强') THEN '是'
         ELSE '否' END AS needs_retest_box,
    CASE WHEN a.evid_max = 0 THEN '无有效证据'
         WHEN b.power_status_ IN ('偏弱','偏强') THEN '功率不合格'
         ELSE '' END AS retest_reason_box,
    CASE WHEN a.evid_max = 0 THEN '高'
         WHEN b.power_status_ IN ('偏弱','偏强') THEN '高'
         ELSE '' END AS retest_priority_box
FROM best b JOIN agg a ON a.box_key = b.box_key
ORDER BY b.box_key
"""

BOX_COLS = [
    "box_key", "area", "cluster_code", "zone", "hub", "level", "fat",
    "source_file", "sheet", "row", "box_name", "power_dbm", "lat", "lon", "address",
    "test_date", "ocr_at", "conf", "photo_status", "power_check", "power_status_", "main_issue",
    "records_total", "files_seen", "has_evidence",
    "needs_retest_box", "retest_reason_box", "retest_priority_box",
]


def query_boxes(conn):
    """跑盒子级查询，返回 dict 列表（列同 BOX_COLS）。CSV 导出(Task 1.2)复用这里。"""
    return [dict(zip(BOX_COLS, r)) for r in conn.execute(BOXES_SQL)]


def count_unparsed(conn):
    return conn.execute(f"SELECT COUNT(*) FROM records WHERE NOT ({PARSED_WHERE})").fetchone()[0]


def print_summary(boxes, unparsed):
    total = len(boxes)
    with_evid = sum(1 for b in boxes if b["has_evidence"] == "是")
    retest = [b for b in boxes if b["needs_retest_box"] == "是"]
    by_reason = {}
    for b in retest:
        by_reason[b["retest_reason_box"]] = by_reason.get(b["retest_reason_box"], 0) + 1
    with_zone = sum(1 for b in boxes if b["zone"] is not None)

    print(f"唯一盒子数:        {total}")
    print(f"有验证证据的盒子:  {with_evid}")
    print(f"需复测盒子:        {len(retest)}")
    for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        print(f"  - {reason}: {n}")
    print(f"unparsed 记录数:   {unparsed}")
    print(f"box_key 带 Z 段:   {with_zone}")
    print(f"box_key 不带 Z 段: {total - with_zone}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("boxes")
    p.add_argument("--db", default="pt_data.sqlite")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    if args.cmd == "boxes":
        boxes = query_boxes(conn)
        print_summary(boxes, count_unparsed(conn))
    conn.close()


if __name__ == "__main__":
    main()
