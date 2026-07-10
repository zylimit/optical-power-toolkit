# -*- coding: utf-8 -*-
"""pt_recheck 硬样本复核——非硬样本行静默劣化的两层防线回归锁（M2 缺陷）。

断言依据 DEV-PLAN.md Task 3.2 验收标准原文，两句是两层要求：
- "复核不劣化：非复核行的数据不得因复核流程受损" —— 预防层（硬约束）：
  process_file 在 store 之前做 OCR json 完整性校验，应有 json 的行缺任何一行
  立即抛 RuntimeError 中止，store 不执行、库内保持原样
- "若个别行变差，打印明细供人工裁定" —— 检测层（兜底）：json 落地但内容本身
  劣化（完整性校验拦不住）时，diff_stats 全量扫描 before 的 worse 明细必须捕获

三个独立场景（不共用破坏性 fixture）：
1. 干净路径：json 全部正常落地 -> 硬样本升级、非硬样本原样保留（happy path 不误伤）
2. json 漏落地：result_path 包装把 row=2 的补齐 json 引出 ocr 目录（非 .json 后缀，
   load_ocr 的 glob 扫不到）-> 预防层在 store 前抛 RuntimeError，整库分毫未动
3. 内容劣化：rebuild_json 包装把 row=2 的重建内容置 legible=False（json 正常落地、
   完整性校验通过，reconcile 会判成"有图模糊"）-> store 真实写入劣化状态，
   检测层 worse 必须命中 row=2

fixture 全部库内造（sqlite3 内存库 + pt_db.SCHEMA），不碰真实库、不发网络请求：
- extract_file mock 掉（不解析真 xlsx，返回手工构造的两行 extracted 记录）
- ocr_one mock 掉（不调 Gemini，硬样本 row=1 返回构造好的清晰 OCR 结果）
运行：python -m pytest tests/test_pt_recheck.py -v
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import pt_db       # noqa: E402  只用它的 SCHEMA，保证测试库与生产建表语句同源
import pt_recheck  # noqa: E402

FNAME = "test.xlsx"
BOX = "AJAHC1Z1H1L1S1"  # 含合法 H#L#S# 后缀，能过 build_records 的 suffix 过滤

# row=2（非硬样本）复核前库内的关键字段——测试要断言它们复核后原样保留
ROW2_LAT, ROW2_LON = 6.5, 3.3
ROW2_ADDR = "12 Adeola Street, Lagos"


def _extracted_row(row):
    """构造 pt_extract.extract_file 单行返回结构（字段名对齐 pt_extract.py:193-202）。
    row=2 也 has_photo=True 且有 image：模拟"该走 rebuild_json 补 json"的正常路径。"""
    return {"source_file": FNAME, "sheet": "Sheet1", "row": row,
            "cluster": "Ajah C1 Z1", "box_table": BOX,
            "power_table": 20.5, "date_table": "2026-07-01", "passfail_table": "Pass",
            "image": f"fake_img_row{row}.jpg", "has_photo": True}


def _fake_ocr_one(item, api_key, model, retries=5, downscale=0):
    """mock pt_ocr.ocr_one：硬样本 row=1 返回清晰可读、标牌与表格一致的 OCR 结果
    （结构对齐 pt_ocr.py:96-104）。row=2 非硬样本不该被 OCR，误调直接报错。"""
    if item["row"] != 1:
        raise AssertionError(f"非硬样本 row={item['row']} 不应真跑 OCR")
    return {"source_file": FNAME, "row": 1, "sheet": "Sheet1",
            "box_name_image": BOX, "power_dbm": -20.5,
            "lat": 6.44, "lon": 3.31, "address": "5 Marina Road, Lagos",
            "addr_area": "Ajah", "addr_estate": "", "addr_street": "Marina Road",
            "near_street": False, "timestamp": "2026-07-02", "legible": True, "notes": ""}


class TestNonHardRowSilentLoss(unittest.TestCase):
    """M2 两层防线：非硬样本 row=2 在漏补 / 内容劣化两种诱因下都不得静默受损。"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(pt_db.SCHEMA)
        common = {"source_file": FNAME, "sheet": "Sheet1", "box_name": BOX,
                  "power_dbm": 20.5, "test_date": "2026-07-01",
                  "model": "gemini-3.5-flash", "ocr_at": "2026-07-01 00:00:00",
                  "addr_area": "", "addr_estate": "", "addr_street": ""}
        # row=1：硬样本（有图模糊 + 标牌糊未核对），本轮复核对象
        self._insert(dict(common, row=1, photo_status="有图模糊", box_check="标牌糊未核对",
                          lat=None, lon=None, address="", box_image=None, power_image_dbm=None))
        # row=2：非硬样本（有图清晰 + 通过），带坐标和地址——复核不该动它
        self._insert(dict(common, row=2, photo_status="有图清晰", box_check="通过",
                          lat=ROW2_LAT, lon=ROW2_LON, address=ROW2_ADDR,
                          addr_street="Adeola Street",
                          box_image=BOX, power_image_dbm=20.5))
        # args.dir 下要有 test.xlsx 实体文件（process_file 会 isfile + getsize，内容不读）
        self.tmpdir = tempfile.TemporaryDirectory(prefix="pt_recheck_test_")
        self.addCleanup(self.tmpdir.cleanup)
        with open(os.path.join(self.tmpdir.name, FNAME), "wb") as f:
            f.write(b"dummy")
        self.args = SimpleNamespace(dir=self.tmpdir.name, model="gemini-test", workers=1)

    def tearDown(self):
        self.conn.close()

    def _insert(self, rec):
        cols = ",".join(rec)
        ph = ",".join("?" * len(rec))
        self.conn.execute(f"INSERT INTO records ({cols}) VALUES ({ph})", list(rec.values()))

    def _run_recheck(self, missed_json=False, degraded_rebuild=False):
        """端到端调真实 process_file，仅 mock 照片抽取与 OCR 网络调用。
        missed_json：result_path 包装把 row=2 的补齐 json 引出 ocr 目录，
        复现"漏补"（预防层诱因）。degraded_rebuild：rebuild_json 包装把 row=2
        的重建内容置 legible=False，复现"json 落地但内容劣化"（检测层诱因）。"""
        real_result_path = pt_recheck.result_path
        real_rebuild_json = pt_recheck.rebuild_json

        def wrapped_result_path(out_dir, r):
            p = real_result_path(out_dir, r)
            if missed_json and r.get("row") == 2:  # row=2 只出现在补齐分支
                return p + ".misplaced"  # 非 .json 后缀，load_ocr 的 glob 扫不到
            return p

        def wrapped_rebuild_json(fname, d):
            j = real_rebuild_json(fname, d)
            if degraded_rebuild and d["row"] == 2:
                j["legible"] = False  # reconcile: 有图 + legible=False -> "有图模糊"
            return j

        hard_keys = {("Sheet1", 1)}
        extracted = [_extracted_row(1), _extracted_row(2)]
        with patch.object(pt_recheck, "extract_file", return_value=extracted), \
             patch.object(pt_recheck, "ocr_one", side_effect=_fake_ocr_one), \
             patch.object(pt_recheck, "result_path", side_effect=wrapped_result_path), \
             patch.object(pt_recheck, "rebuild_json", side_effect=wrapped_rebuild_json):
            res = pt_recheck.process_file(self.conn, FNAME, hard_keys, self.args, "fake-key")
        self.assertIsNotNone(res, "process_file 不应跳过该文件")
        return res  # before 快照（该文件复核前全部行）

    def _all_rows(self):
        """全库关键字段快照（两行都取），用于断言"DB 完全未被写入"。"""
        return self.conn.execute(
            "SELECT row, photo_status, box_check, lat, lon, address, model, ocr_at "
            "FROM records WHERE source_file=? ORDER BY row", (FNAME,)).fetchall()

    def _row2(self):
        return self.conn.execute(
            "SELECT lat, lon, address, photo_status, box_check FROM records "
            "WHERE source_file=? AND row=2", (FNAME,)).fetchone()

    def test_hard_row_recheck_upgrades(self):
        """场景1 干净路径：json 全部正常落地，完整性校验通过、store 正常执行。
        硬样本 row=1 用 mock 的清晰 OCR 复核后升级为 有图清晰/通过；
        非硬样本 row=2 经 rebuild_json 反查重建后原样保留（happy path 不误伤）。"""
        before = self._run_recheck()
        self.assertEqual(before, {("Sheet1", 1): ("有图模糊", "标牌糊未核对"),
                                  ("Sheet1", 2): ("有图清晰", "通过")})  # before 覆盖全部两行
        p, b = self.conn.execute(
            "SELECT photo_status, box_check FROM records WHERE source_file=? AND row=1",
            (FNAME,)).fetchone()
        self.assertEqual((p, b), ("有图清晰", "通过"))
        self.assertEqual(self._row2(), (ROW2_LAT, ROW2_LON, ROW2_ADDR, "有图清晰", "通过"),
                         "干净路径下非硬样本 row=2 必须原样保留，不得被复核误伤")

    def test_missing_json_aborts_and_preserves_state(self):
        """场景2 预防层："复核不劣化"是硬约束——row=2 的补齐 json 漏落地时，
        process_file 必须在 store 之前抛 RuntimeError 中止，该文件这次复核对 DB
        零写入：不只是 row=2 没受伤，row=1 也不得被复核升级（store 根本没跑）。"""
        baseline = self._all_rows()
        with self.assertRaises(RuntimeError) as cm:
            self._run_recheck(missed_json=True)
        msg = str(cm.exception)
        self.assertIn(FNAME, msg, "异常消息应指明是哪个文件")
        self.assertIn(str(("Sheet1", 2)), msg, "异常消息应列出缺失的 (sheet, row)")
        self.assertEqual(self._all_rows(), baseline,
                         "预防层生效 = store 未执行，DB 必须与复核前完全一致")

    def test_non_hard_row_degradation_must_be_flagged(self):
        """场景3 检测层：row=2 的补齐 json 正常落地（完整性校验拦不住），但内容
        本身劣化（legible=False -> reconcile 判"有图模糊"）。store 真实写入劣化
        状态后，全量扫描 before 的 worse 明细必须命中 row=2，不得静默。"""
        before = self._run_recheck(degraded_rebuild=True)
        n, leg, box, worse = pt_recheck.diff_stats(self.conn, FNAME, before, {("Sheet1", 1)})
        # fixture 自证：row=2 确实被写成劣化状态（清晰 -> 模糊），检测层有真实靶子
        self.assertEqual(self._row2()[3], "有图模糊", "fixture 应真实造成 row=2 内容劣化")
        # 核心断言：非硬样本行的劣化必须被变差检测捕获供人工裁定（M2 回归锁）
        worse_keys = {k for k, *_ in worse}
        self.assertIn(("Sheet1", 2), worse_keys,
                      "非硬样本 row=2 内容劣化，必须被变差检测捕获供人工裁定，"
                      f"实际 worse 明细只有: {sorted(worse_keys)}")


if __name__ == "__main__":
    unittest.main()
