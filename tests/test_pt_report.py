# -*- coding: utf-8 -*-
"""pt_report boxes 盒子级去重报表——回归测试。

断言依据 DEV-PLAN.md Phase 1 规格（不是 pt_report.py 实现）：
- box_key = area+COALESCE(cluster_code,'')+COALESCE(zone,'')+hub+level+fat
- area/hub/level/fat 任一 NULL → 不进 boxes、进 unparsed；zone NULL 不影响归一
- 最佳记录排序：evidence DESC → conf(高>中>低>无效) → ocr_at DESC
- evidence 定义：photo_status='有图清晰' AND power_check IN ('一致','表缺已恢复')
- has_evidence 看全盒任一记录，不看最佳行
- 复测三分支：无证据→(是,无有效证据,高)；有证据但最佳 power_status_ 偏弱/偏强→(是,功率不合格,高)；其余→(否,'','')
- boxes CSV 23 列固定序 / unparsed CSV 5 列

fixture 全部库内造（sqlite3 内存库 + pt_db.SCHEMA），不碰真实库。
运行：python -m unittest discover -s tests -v
"""

import csv
import os
import sqlite3
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import pt_db      # noqa: E402  只用它的 SCHEMA，保证测试库与生产建表语句同源
import pt_report  # noqa: E402

# DEV-PLAN Phase 1 钉死的 boxes CSV 列（23 列固定顺序）——独立抄自规格文档
SPEC_BOXES_CSV_COLS = [
    "box_key", "area", "cluster_code", "zone", "hub", "level", "fat",
    "records_total", "files_seen", "best_source_file", "best_sheet", "best_row", "best_conf",
    "power_dbm", "power_status_", "lat", "lon", "address", "test_date", "has_evidence",
    "needs_retest_box", "retest_reason_box", "retest_priority_box",
]
SPEC_UNPARSED_CSV_COLS = ["box_name", "source_file", "sheet", "row", "main_issue"]


class PtReportTestBase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(pt_db.SCHEMA)
        self._row_seq = 0

    def tearDown(self):
        self.conn.close()

    def insert(self, **kw):
        """插一条 records 行。默认值构成一条「可归一 + 有证据 + 功率正常」的干净记录，
        各用例只覆写自己关心的列。UNIQUE(source_file,sheet,row) 用自增 row 规避。"""
        self._row_seq += 1
        rec = {
            "source_file": "f1.xlsx", "sheet": "S1", "row": self._row_seq,
            "box_name": "BOX", "power_dbm": -20.5, "lat": 6.5, "lon": 3.3,
            "address": "addr", "test_date": "2026-07-01",
            "ocr_at": "2026-07-01 00:00:00", "conf": "高",
            "photo_status": "有图清晰", "power_check": "一致",
            "power_status_": "正常", "main_issue": "",
            "area": "DST", "cluster_code": "C2", "zone": "Z1",
            "hub": "H1", "level": "L1", "fat": "FAT01",
        }
        rec.update(kw)
        cols = ",".join(rec)
        ph = ",".join("?" * len(rec))
        self.conn.execute(f"INSERT INTO records ({cols}) VALUES ({ph})", list(rec.values()))
        return rec

    def boxes(self, where=None):
        return pt_report.query_boxes(self.conn, where)

    def one_box(self, boxes, **ident):
        hit = [b for b in boxes if all(b[k] == v for k, v in ident.items())]
        self.assertEqual(len(hit), 1, f"期望恰好 1 个盒子匹配 {ident}，实际 {len(hit)}")
        return hit[0]


class TestBestRecordOrdering(PtReportTestBase):
    """用例1：最佳记录排序 evidence DESC → conf → ocr_at DESC。"""

    def test_evidence_beats_conf_then_conf_beats_time(self):
        # 同一盒 3 条：A 无证据但 conf 最高且最新；B 有证据 conf 低 最旧；C 有证据 conf 中
        self.insert(row=101, power_check="图糊用表", conf="高",
                    ocr_at="2026-07-03 00:00:00", power_dbm=-11.0)   # A: evidence=0
        self.insert(row=102, conf="低", ocr_at="2026-07-01 00:00:00", power_dbm=-12.0)  # B
        self.insert(row=103, conf="中", ocr_at="2026-07-02 00:00:00", power_dbm=-13.0)  # C
        boxes = self.boxes()
        self.assertEqual(len(boxes), 1)
        best = boxes[0]
        # evidence 优先压过 conf=高；有证据内部 conf 中 胜 低 —— 最佳应为 C(row=103)
        self.assertEqual(best["row"], 103)
        self.assertEqual(best["conf"], "中")
        self.assertEqual(best["power_dbm"], -13.0)

    def test_ocr_at_breaks_tie(self):
        # evidence、conf 全同 → ocr_at 最新者胜
        self.insert(row=201, ocr_at="2026-07-01 00:00:00")
        self.insert(row=202, ocr_at="2026-07-05 00:00:00")
        best = self.boxes()[0]
        self.assertEqual(best["row"], 202)


class TestHasEvidenceBoxLevel(PtReportTestBase):
    """用例2：has_evidence 看全盒任一记录，而非仅最佳行。"""

    def test_all_rows_without_evidence_is_no(self):
        self.insert(fat="FA", photo_status="有图模糊")
        self.insert(fat="FA", power_check="图糊用表")
        box = self.one_box(self.boxes(), fat="FA")
        self.assertEqual(box["has_evidence"], "否")

    def test_any_row_with_evidence_is_yes(self):
        self.insert(fat="FB", photo_status="有图模糊", conf="高")
        self.insert(fat="FB", conf="低")  # 默认=有证据，虽 conf 低
        box = self.one_box(self.boxes(), fat="FB")
        self.assertEqual(box["has_evidence"], "是")


class TestRetestThreeBranches(PtReportTestBase):
    """用例3：复测判定三分支（needs/reason/priority 三列成组断言）。"""

    def test_no_evidence_branch(self):
        self.insert(fat="FA", photo_status="有图模糊", power_status_="正常")
        box = self.one_box(self.boxes(), fat="FA")
        self.assertEqual(
            (box["needs_retest_box"], box["retest_reason_box"], box["retest_priority_box"]),
            ("是", "无有效证据", "高"))

    def test_evidence_but_weak_power_branch(self):
        self.insert(fat="FB", power_status_="偏弱")
        box = self.one_box(self.boxes(), fat="FB")
        self.assertEqual(
            (box["needs_retest_box"], box["retest_reason_box"], box["retest_priority_box"]),
            ("是", "功率不合格", "高"))

    def test_evidence_but_strong_power_branch(self):
        # 规格：power_status_ IN ('偏弱','偏强') 都算功率不合格
        self.insert(fat="FC", power_status_="偏强")
        box = self.one_box(self.boxes(), fat="FC")
        self.assertEqual(
            (box["needs_retest_box"], box["retest_reason_box"], box["retest_priority_box"]),
            ("是", "功率不合格", "高"))

    def test_evidence_and_pass_branch(self):
        self.insert(fat="FD", power_status_="正常")
        box = self.one_box(self.boxes(), fat="FD")
        self.assertEqual(
            (box["needs_retest_box"], box["retest_reason_box"], box["retest_priority_box"]),
            ("否", "", ""))


class TestUnparsedRouting(PtReportTestBase):
    """用例4：area/hub/level/fat 任一 NULL → unparsed；zone NULL 不影响归一。"""

    def test_missing_hierarchy_goes_unparsed(self):
        self.insert(area=None)
        self.insert(hub=None)
        self.insert(level=None)
        self.insert(fat=None)
        self.assertEqual(len(self.boxes()), 0, "缺层级段的记录不得进 boxes")
        self.assertEqual(pt_report.count_unparsed(self.conn), 4)

    def test_zone_null_still_parsed(self):
        self.insert(zone=None)
        boxes = self.boxes()
        self.assertEqual(len(boxes), 1, "zone NULL（省 Z 变体）仍应归一进 boxes")
        self.assertEqual(pt_report.count_unparsed(self.conn), 0)


class TestBoxKeyGrouping(PtReportTestBase):
    """用例5：box_key 撞并与拆分。"""

    def test_same_identity_merges(self):
        self.insert(source_file="f1.xlsx")
        self.insert(source_file="f2.xlsx")
        boxes = self.boxes()
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0]["records_total"], 2)
        self.assertEqual(boxes[0]["files_seen"], 2)

    def test_zone_variant_splits(self):
        # 仅 zone 不同（Z1 vs NULL）→ 两个盒（Phase 1 明示不做跨键合并）
        self.insert(zone="Z1")
        self.insert(zone=None)
        self.assertEqual(len(self.boxes()), 2)


class TestCsvContract(PtReportTestBase):
    """用例6：CSV 表头契约 == DEV-PLAN 钉死列序。"""

    def _read_header(self, path):
        with open(path, encoding="utf-8-sig", newline="") as f:
            return next(csv.reader(f))

    def test_boxes_csv_header_23_cols(self):
        self.insert()
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "boxes.csv")
            n = pt_report.export_boxes(self.boxes(), out)
            header = self._read_header(out)
        self.assertEqual(n, 1)
        self.assertEqual(len(header), 23)
        for i, (got, want) in enumerate(zip(header, SPEC_BOXES_CSV_COLS)):
            self.assertEqual(got, want, f"第 {i+1} 列应为 {want}，实际 {got}")

    def test_unparsed_csv_header(self):
        self.insert(area=None, box_name="裸名盒", main_issue="层级不可解析")
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "unparsed.csv")
            n = pt_report.export_unparsed(self.conn, out)
            header = self._read_header(out)
        self.assertEqual(n, 1)
        self.assertEqual(header, SPEC_UNPARSED_CSV_COLS)


class TestEvidenceDefinition(PtReportTestBase):
    """用例7：evidence 定义两条件缺一不可。"""

    def test_clear_photo_and_consistent_is_evidence(self):
        self.insert(fat="FA", photo_status="有图清晰", power_check="一致")
        self.assertEqual(self.one_box(self.boxes(), fat="FA")["has_evidence"], "是")

    def test_blurry_photo_is_not_evidence(self):
        self.insert(fat="FB", photo_status="有图模糊", power_check="一致")
        self.assertEqual(self.one_box(self.boxes(), fat="FB")["has_evidence"], "否")

    def test_inconsistent_check_is_not_evidence(self):
        self.insert(fat="FC", photo_status="有图清晰", power_check="图糊用表")
        self.assertEqual(self.one_box(self.boxes(), fat="FC")["has_evidence"], "否")

    def test_meter_recovered_is_evidence(self):
        # 规格：power_check IN ('一致','表缺已恢复') 都算证据
        self.insert(fat="FD", photo_status="有图清晰", power_check="表缺已恢复")
        self.assertEqual(self.one_box(self.boxes(), fat="FD")["has_evidence"], "是")


if __name__ == "__main__":
    unittest.main()
