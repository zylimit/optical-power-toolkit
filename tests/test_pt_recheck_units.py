# -*- coding: utf-8 -*-
"""pt_recheck 单元级回归锁——补齐 test_pt_recheck.py（M2 两层防线）未覆盖的三块：

1. fetch_hard 硬样本筛选：模块 docstring 声明"从库里筛硬样本行（有图模糊 / OCR失败 /
   标牌糊未核对）"。SQL WHERE 写错会让整条复核流水静默空转（一行都筛不出、不报错），
   必须锁：三种命中条件各筛得到、干净行筛不进、按文件分组、--limit 截文件数不截行数。
2. diff_stats 三项计数：复核行数 n / legible 假转真 leg / box_check 非通过转通过 box
   只统计硬样本行；行消失要进 worse 明细。计数错会误导人工裁定（虚报复核成效）。
3. process_file 临时目录清理：docstring 承诺"图片和临时 OCR json 绝不残留"——
   正常返回和完整性校验抛 RuntimeError 两条路径都必须删干净临时目录。

fixture 全部库内造（sqlite3 内存库 + pt_db.SCHEMA），不碰真实库、不发网络请求。
运行：python -m pytest tests/test_pt_recheck_units.py -v
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
for p in (SCRIPTS, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import pt_db        # noqa: E402
import pt_recheck   # noqa: E402
from test_pt_recheck import FNAME, _extracted_row, _fake_ocr_batch  # noqa: E402


def _mem_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(pt_db.SCHEMA)
    return conn


def _insert(conn, **rec):
    rec.setdefault("sheet", "Sheet1")
    cols = ",".join(rec)
    ph = ",".join("?" * len(rec))
    conn.execute(f"INSERT INTO records ({cols}) VALUES ({ph})", list(rec.values()))


class TestFetchHard(unittest.TestCase):
    """硬样本筛选条件：有图模糊 / OCR失败 / box_check=标牌糊未核对，三者取或。"""

    def setUp(self):
        self.conn = _mem_db()
        self.addCleanup(self.conn.close)
        # a.xlsx：三种命中条件各一行 + 一行干净行（不该被筛进）
        _insert(self.conn, source_file="a.xlsx", row=1, photo_status="有图模糊", box_check="通过")
        _insert(self.conn, source_file="a.xlsx", row=2, photo_status="OCR失败", box_check="通过")
        _insert(self.conn, source_file="a.xlsx", row=3, photo_status="有图清晰", box_check="标牌糊未核对")
        _insert(self.conn, source_file="a.xlsx", row=4, photo_status="有图清晰", box_check="通过")
        # b.xlsx：一行命中（验证按文件分组 + limit 截文件）
        _insert(self.conn, source_file="b.xlsx", row=1, photo_status="有图模糊", box_check="通过")

    def test_three_hard_conditions_hit_and_clean_row_excluded(self):
        by_file = pt_recheck.fetch_hard(self.conn)
        self.assertEqual(by_file, {
            "a.xlsx": {("Sheet1", 1), ("Sheet1", 2), ("Sheet1", 3)},
            "b.xlsx": {("Sheet1", 1)},
        }, "有图模糊/OCR失败/标牌糊未核对 三种条件都要命中，干净行（有图清晰+通过）不得混入")

    def test_limit_truncates_files_not_rows(self):
        by_file = pt_recheck.fetch_hard(self.conn, limit=1)
        self.assertEqual(sorted(by_file), ["a.xlsx"], "--limit 按文件名排序截取文件数")
        self.assertEqual(len(by_file["a.xlsx"]), 3, "limit 截的是文件数，不该丢文件内的硬样本行")

    def test_codex_processed_rows_excluded(self):
        """codex 处理过的模糊行没有复核价值（同模型重跑结果不变），不得再筛出；
        gemini 处理的照常筛出；model 为 NULL（setUp 的旧数据）视作未被 codex 处理。"""
        _insert(self.conn, source_file="c.xlsx", row=1,
                photo_status="有图模糊", box_check="通过", model="codex-gpt-5.6-terra")
        _insert(self.conn, source_file="c.xlsx", row=2,
                photo_status="有图模糊", box_check="通过", model="gemini-3.5-flash")
        by_file = pt_recheck.fetch_hard(self.conn)
        self.assertEqual(by_file.get("c.xlsx"), {("Sheet1", 2)},
                         "codex 行不得筛出，gemini 行照常筛出")
        self.assertIn("a.xlsx", by_file, "model 为 NULL 的旧行必须照常筛出")


class TestDiffStats(unittest.TestCase):
    """三项计数只统计硬样本行；worse 全量扫描含行消失。"""

    def test_counts_and_row_disappearance(self):
        conn = _mem_db()
        self.addCleanup(conn.close)
        f = "c.xlsx"
        # after 状态（库内当前值）
        _insert(conn, source_file=f, row=1, photo_status="有图清晰", box_check="通过")   # 硬样本，双升级
        _insert(conn, source_file=f, row=2, photo_status="有图模糊", box_check="标牌糊未核对")  # 硬样本，没升级
        _insert(conn, source_file=f, row=3, photo_status="有图清晰", box_check="通过")   # 非硬样本，原样
        # row=4 不插：before 有、after 消失
        before = {
            ("Sheet1", 1): ("有图模糊", "标牌糊未核对"),
            ("Sheet1", 2): ("OCR失败", "标牌糊未核对"),
            ("Sheet1", 3): ("有图清晰", "通过"),
            ("Sheet1", 4): ("有图清晰", "通过"),
        }
        hard = {("Sheet1", 1), ("Sheet1", 2)}
        n, leg, box, worse = pt_recheck.diff_stats(conn, f, before, hard)
        self.assertEqual(n, 2, "复核行数 = 命中 hard_keys 的行数")
        self.assertEqual(leg, 1, "legible 假转真：仅 row=1（有图模糊->有图清晰）")
        self.assertEqual(box, 1, "box_check 非通过转通过：仅 row=1")
        self.assertEqual([k for k, *_ in worse], [("Sheet1", 4)],
                         "行消失必须进 worse 明细供人工裁定，未变差的行不得误报")

    def test_hard_row_already_passed_box_not_counted(self):
        """box 计数条件是"非通过 -> 通过"：复核前已是通过的硬样本行不得虚增计数。"""
        conn = _mem_db()
        self.addCleanup(conn.close)
        f = "d.xlsx"
        _insert(conn, source_file=f, row=1, photo_status="有图清晰", box_check="通过")
        before = {("Sheet1", 1): ("有图模糊", "通过")}  # box 本来就是通过
        n, leg, box, worse = pt_recheck.diff_stats(conn, f, before, {("Sheet1", 1)})
        self.assertEqual((n, leg, box, worse), (1, 1, 0, []),
                         "box_check 复核前后都是通过，不算'转通过'")


class TestTempDirCleanup(unittest.TestCase):
    """process_file docstring 承诺：图片和临时 OCR json 绝不残留——成功和中止两条路都要删。"""

    def setUp(self):
        self.conn = _mem_db()
        self.addCleanup(self.conn.close)
        common = {"source_file": FNAME, "box_name": "AJAHC1Z1H1L1S1",
                  "power_dbm": 20.5, "test_date": "2026-07-01",
                  "model": "gemini-3.5-flash", "ocr_at": "2026-07-01 00:00:00",
                  "addr_area": "", "addr_estate": "", "addr_street": ""}
        _insert(self.conn, **common, row=1, photo_status="有图模糊", box_check="标牌糊未核对",
                lat=None, lon=None, address="", box_image=None, power_image_dbm=None)
        _insert(self.conn, **common, row=2, photo_status="有图清晰", box_check="通过",
                lat=6.5, lon=3.3, address="12 Adeola Street, Lagos",
                box_image="AJAHC1Z1H1L1S1", power_image_dbm=20.5)
        self.tmpdir = tempfile.TemporaryDirectory(prefix="pt_recheck_test_")
        self.addCleanup(self.tmpdir.cleanup)
        with open(os.path.join(self.tmpdir.name, FNAME), "wb") as f:
            f.write(b"dummy")
        self.args = SimpleNamespace(dir=[self.tmpdir.name], model="gemini-test", workers=1, backend="fake", downscale=0)

    def _run(self, extra_patches=()):
        """跑真实 process_file，捕获它建的临时目录路径（挂 self.created，
        异常路径下测试方法也拿得到）。"""
        self.created = []
        real_mkdtemp = tempfile.mkdtemp

        def spy_mkdtemp(*a, **kw):
            d = real_mkdtemp(*a, **kw)
            self.created.append(d)
            return d

        ctx = [patch.object(pt_recheck.tempfile, "mkdtemp", side_effect=spy_mkdtemp),
               patch.object(pt_recheck, "extract_file",
                            return_value=[_extracted_row(1), _extracted_row(2)])]
        ctx.extend(extra_patches)
        try:
            for c in ctx:
                c.start()
            result = pt_recheck.process_file(
                self.conn, FNAME, {("Sheet1", 1)}, self.args, "fake-backend", _fake_ocr_batch)
        finally:
            for c in ctx:
                c.stop()
        self.assertEqual(len(self.created), 1, "process_file 应恰好建一个临时目录")
        return result

    def test_temp_dir_removed_on_success(self):
        result = self._run()
        self.assertIsNotNone(result, "干净路径 process_file 不应跳过")
        self.assertFalse(os.path.exists(self.created[0]),
                         "成功路径复核完成后临时目录（照片 + 临时 OCR json）必须删除")
        # 顺带锁溯源还原：未复核行还原原 model/ocr_at（不冒充 pro 复核过），复核行记新模型
        rows = dict(self.conn.execute(
            "SELECT row, model FROM records WHERE source_file=?", (FNAME,)).fetchall())
        self.assertEqual(rows[1], "gemini-test", "复核行 row=1 的 model 应记本次复核模型")
        self.assertEqual(rows[2], "gemini-3.5-flash",
                         "未复核行 row=2 的 model 必须还原为原值，不得冒充被 pro 复核过")

    def test_temp_dir_removed_on_integrity_abort(self):
        """完整性校验中止（RuntimeError）路径同样不得残留临时目录。
        load_ocr mock 成空 -> 应有 json 的行全部'缺失' -> store 之前抛 RuntimeError。"""
        with self.assertRaises(RuntimeError):
            self._run(extra_patches=[
                patch.object(pt_recheck, "load_ocr", return_value=[])])
        self.assertEqual(len(self.created), 1, "process_file 应恰好建一个临时目录")
        self.assertFalse(os.path.exists(self.created[0]),
                         "完整性校验中止路径同样必须清理临时目录，不得残留照片/临时 json")


if __name__ == "__main__":
    unittest.main()
