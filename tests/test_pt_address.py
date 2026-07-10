# -*- coding: utf-8 -*-
"""Phase 2 地址结构化——回归测试。

断言依据 DEV-PLAN.md Phase 2 规格（不是实现代码）：
- standardize_street(street, near_street=False)：
  空/None/含 unnamed 或 no street name 标记 → 返回空串；
  near_street=True 加 "Off " 前缀（例 Off 13 Ogundimu St）；
  已以 Off 开头（不分大小写）不重复加
- reconcile()：从 OCR 结果读 addr_area/addr_estate/addr_street/near_street，
  street 经 standardize_street 处理，返回 dict 增加三个键（字符串，无值为空串）；
  孤立 "Off"（无后续街名）视为无效街名；near_street 缺失/None 不报错按 False 处理
- pt_db：SCHEMA 的 records 表含 addr_area/addr_estate/addr_street 三列（TEXT）；
  connect() 打开旧库自动 ALTER 补列且幂等；build_records() 把 reconcile
  返回的三个新键写入 base dict

fixture 全部临时造（sqlite3 内存库 / tempfile 临时库 + 临时 ocr 目录），不碰真实库。
运行：python -m unittest discover -s tests -v
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import pt_db     # noqa: E402
import pt_merge  # noqa: E402
import pt_rules  # noqa: E402

ADDR_COLS = ["addr_area", "addr_estate", "addr_street"]  # 规格钉死的三个新列名


class TestStandardizeStreet(unittest.TestCase):
    """standardize_street 纯函数——街名标准化契约。"""

    def test_none_and_empty_return_empty(self):
        self.assertEqual(pt_rules.standardize_street(None), "")
        self.assertEqual(pt_rules.standardize_street(""), "")
        self.assertEqual(pt_rules.standardize_street("   "), "")
        # near_street=True 也不能凭空造出 "Off "
        self.assertEqual(pt_rules.standardize_street(None, True), "")
        self.assertEqual(pt_rules.standardize_street("", True), "")

    def test_no_street_markers_case_insensitive(self):
        for bad in ("no street name", "No Street Name", "NO STREET NAME",
                    "no name", "No Name", "unnamed", "Unnamed Road", "UNNAMED"):
            self.assertEqual(pt_rules.standardize_street(bad), "",
                             f"含无街名标记 {bad!r} 应返回空串")
            self.assertEqual(pt_rules.standardize_street(bad, True), "",
                             f"含无街名标记 {bad!r} 即使 near_street=True 也应返回空串")

    def test_normal_street_not_near_unchanged(self):
        self.assertEqual(pt_rules.standardize_street("13 Ogundimu St", False),
                         "13 Ogundimu St")
        self.assertEqual(pt_rules.standardize_street("13 Ogundimu St"),
                         "13 Ogundimu St")

    def test_normal_street_near_gets_off_prefix(self):
        # DEV-PLAN 验收例：('13 Ogundimu St', True) → 'Off 13 Ogundimu St'
        self.assertEqual(pt_rules.standardize_street("13 Ogundimu St", True),
                         "Off 13 Ogundimu St")

    def test_already_off_near_no_duplicate_prefix(self):
        # DEV-PLAN 验收例：('Off 13 Ogundimu St', True) → 不重复加（不分大小写）
        for s in ("Off 13 Ogundimu St", "off 13 Ogundimu St", "OFF 13 Ogundimu St"):
            self.assertEqual(pt_rules.standardize_street(s, True), s,
                             f"{s!r} 已以 Off 开头，near_street=True 不得重复加前缀")

    def test_already_off_not_near_kept_as_is(self):
        # 已有的 Off 前缀不因 near_street=False 被剥除
        self.assertEqual(pt_rules.standardize_street("Off 13 Ogundimu St", False),
                         "Off 13 Ogundimu St")

    def test_offlike_word_is_not_off_prefix(self):
        # "Offa Road"（Off 后无空格）不是 Off 前缀，near_street=True 仍应加前缀
        self.assertEqual(pt_rules.standardize_street("Offa Road", True),
                         "Off Offa Road")

    def test_surrounding_whitespace_trimmed(self):
        self.assertEqual(pt_rules.standardize_street("  13 Ogundimu St  ", True),
                         "Off 13 Ogundimu St")


class TestReconcileAddressFields(unittest.TestCase):
    """reconcile() 地址三段——透传、孤立 Off、near_street 杂质容错。"""

    @staticmethod
    def rec(o=None, r=None):
        return pt_merge.reconcile(r or {}, o or {})

    def test_normal_three_fields_passthrough(self):
        rec = self.rec({"addr_area": "Alimosho", "addr_estate": "Peace Estate",
                        "addr_street": "13 Ogundimu St", "near_street": False})
        self.assertEqual(rec["addr_area"], "Alimosho")
        self.assertEqual(rec["addr_estate"], "Peace Estate")
        self.assertEqual(rec["addr_street"], "13 Ogundimu St")

    def test_near_street_true_adds_off(self):
        rec = self.rec({"addr_street": "13 Ogundimu St", "near_street": True})
        self.assertEqual(rec["addr_street"], "Off 13 Ogundimu St")

    def test_isolated_off_is_invalid_street(self):
        # 孤立 "Off"（无后续街名）→ 无效，最终为空串；大小写/空白不影响判定
        for junk in ("Off", "off", "OFF", "  Off  "):
            rec = self.rec({"addr_street": junk, "near_street": False})
            self.assertEqual(rec["addr_street"], "",
                             f"孤立 {junk!r} 应视为无效街名")

    def test_isolated_off_with_near_true_stays_empty(self):
        # 孤立 Off 清空后，near_street=True 也不得凭空造出 "Off "
        rec = self.rec({"addr_street": "off", "near_street": True})
        self.assertEqual(rec["addr_street"], "")

    def test_near_street_none_treated_as_false(self):
        # OCR 失败场景 near_street=None → 不报错、不加前缀
        rec = self.rec({"addr_street": "13 Ogundimu St", "near_street": None})
        self.assertEqual(rec["addr_street"], "13 Ogundimu St")

    def test_near_street_falsy_junk_treated_as_false(self):
        for junk in (0, "", False):
            rec = self.rec({"addr_street": "13 Ogundimu St", "near_street": junk})
            self.assertEqual(rec["addr_street"], "13 Ogundimu St",
                             f"near_street={junk!r} 应按 False 处理")

    def test_near_street_truthy_string_junk_treated_as_true(self):
        # bool(x or False) 语义：任何非空字符串都是 True——包括 "false"/"no"。
        # 如实锁定真实行为；语义上是否合理见测试报告（OCR 若把布尔回成
        # 字符串 "false"，这里会被当 True 加前缀）。
        for junk in ("yes", "true", "false", "no"):
            rec = self.rec({"addr_street": "13 Ogundimu St", "near_street": junk})
            self.assertEqual(rec["addr_street"], "Off 13 Ogundimu St",
                             f"near_street={junk!r}（非空字符串）按 bool 语义为 True")

    def test_area_estate_none_become_empty_string(self):
        rec = self.rec({"addr_area": None, "addr_estate": None, "addr_street": None})
        self.assertEqual(rec["addr_area"], "")
        self.assertEqual(rec["addr_estate"], "")
        self.assertEqual(rec["addr_street"], "")

    def test_missing_keys_ocr_absent(self):
        # OCR 完全缺席（o={}，如无照片行）→ 三键存在且为空串，不报错
        rec = self.rec({})
        for k in ADDR_COLS:
            self.assertIn(k, rec, f"reconcile 返回 dict 必须含 {k}")
            self.assertEqual(rec[k], "")

    def test_area_estate_whitespace_stripped(self):
        rec = self.rec({"addr_area": " Alimosho ", "addr_estate": " Peace Estate "})
        self.assertEqual(rec["addr_area"], "Alimosho")
        self.assertEqual(rec["addr_estate"], "Peace Estate")


# 模拟 Phase 2 之前的旧库建表语句：无 addr 三列、无派生列
_OLD_SCHEMA = """
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
    lat REAL, lon REAL, address TEXT,
    test_date TEXT, pass_fail TEXT,
    conf TEXT, photo_status TEXT, power_check TEXT, box_check TEXT,
    has_coord TEXT, has_addr TEXT, fixable_coord TEXT, main_issue TEXT,
    box_image TEXT, power_image_dbm REAL, model TEXT, ocr_at TEXT,
    UNIQUE(source_file, sheet, row)
);
"""


class TestDbSchemaAndMigration(unittest.TestCase):
    """pt_db：建表含新三列 + 旧库自动迁移补列（幂等）。"""

    @staticmethod
    def _cols(conn):
        return {r[1] for r in conn.execute("PRAGMA table_info(records)")}

    def test_schema_has_three_addr_columns(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(pt_db.SCHEMA)
            cols = self._cols(conn)
            for c in ADDR_COLS:
                self.assertIn(c, cols, f"SCHEMA 建出的 records 表缺列 {c}")
        finally:
            conn.close()

    def test_connect_migrates_old_db(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "old.sqlite")
            old = sqlite3.connect(db)
            old.executescript(_OLD_SCHEMA)
            old.execute("INSERT INTO records (source_file, sheet, row, box_name) "
                        "VALUES ('legacy.xlsx', 'S1', 1, 'DSTC1Z3H2L1S4')")
            old.commit()
            old.close()
            self.assertNotIn("addr_area", self._pragma(db), "前置：旧库不应有新列")

            conn = pt_db.connect(db)  # 迁移不得报错
            try:
                cols = self._cols(conn)
                for c in ADDR_COLS:
                    self.assertIn(c, cols, f"connect() 迁移后 records 表缺列 {c}")
                # 旧数据保留，新列回填为 NULL
                row = conn.execute(
                    "SELECT source_file, addr_area, addr_estate, addr_street "
                    "FROM records WHERE row=1").fetchone()
                self.assertEqual(row[0], "legacy.xlsx")
                self.assertEqual(row[1:], (None, None, None))
            finally:
                conn.close()

            # 幂等：再 connect 一次不得因重复 ADD COLUMN 报错
            conn2 = pt_db.connect(db)
            try:
                for c in ADDR_COLS:
                    self.assertIn(c, self._cols(conn2))
            finally:
                conn2.close()

    @staticmethod
    def _pragma(db):
        conn = sqlite3.connect(db)
        try:
            return {r[1] for r in conn.execute("PRAGMA table_info(records)")}
        finally:
            conn.close()


class TestBuildRecordsAddr(unittest.TestCase):
    """pt_db.build_records()：reconcile 的三个新键写入 base dict。"""

    def _run(self, rows, ocr_files):
        with tempfile.TemporaryDirectory() as d:
            for i, o in enumerate(ocr_files):
                with open(os.path.join(d, f"o{i}.json"), "w", encoding="utf-8") as f:
                    json.dump(o, f)
            return pt_db.build_records(rows, d, "test-model", "2026-07-10 00:00:00")

    def test_addr_fields_flow_into_record(self):
        rows = [{"source_file": "f.xlsx", "sheet": "S1", "row": 1,
                 "box_table": "DSTC1Z3H2L1S4", "cluster": "DST C1 Z3"}]
        ocr = [{"source_file": "f.xlsx", "sheet": "S1", "row": 1,
                "legible": True, "box_name_image": "DSTC1Z3H2L1S4",
                "addr_area": "Alimosho", "addr_estate": "Peace Estate",
                "addr_street": "13 Ogundimu St", "near_street": True}]
        recs = self._run(rows, ocr)
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec["addr_area"], "Alimosho")
        self.assertEqual(rec["addr_estate"], "Peace Estate")
        self.assertEqual(rec["addr_street"], "Off 13 Ogundimu St")

    def test_no_ocr_row_gets_empty_addr_fields(self):
        # 行没有对应 OCR json（无图/未跑）→ 三键存在且为空串
        rows = [{"source_file": "f.xlsx", "sheet": "S1", "row": 2,
                 "box_table": "DSTC1Z3H2L1S4"}]
        recs = self._run(rows, [])
        self.assertEqual(len(recs), 1)
        for k in ADDR_COLS:
            self.assertIn(k, recs[0])
            self.assertEqual(recs[0][k], "")

    def test_addr_keys_covered_by_rec_cols(self):
        # 契约：三个新键必须在 REC_COLS 里，否则 upsert 会静默丢字段
        for k in ADDR_COLS:
            self.assertIn(k, pt_db.REC_COLS)
            self.assertIn(k, pt_db.BASE_COLS)


if __name__ == "__main__":
    unittest.main()
