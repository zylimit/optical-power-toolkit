# -*- coding: utf-8 -*-
"""scripts/pt_upload.py 客户端 —— 回归测试。

覆盖上传前的三块纯逻辑（不起服务、不发网络请求）：
- content_md5(path)：对文件内容算 md5，与已知 bytes 的 md5 对上（32 位小写 hex）。
- build_upload_records(rows_json, ocr_dir, model)：复用 pt_db.build_records，
  junk 行（box_table 无 H##L#S# 后缀）过滤、真盒子行保留。
- collect_images(rows_json)：按 rec["image"] 字段收图（字段名是 image 不是 photo），
  has_photo=false 跳过、image 文件不存在跳过、存在则按 (sheet,row) 收。

隔离：临时 rows.json + 空 ocr 目录 + 临时图文件，跑完 tmp_path 自动清。
scripts/ 目录加入 sys.path（pt_upload 依赖 pt_db/pt_merge/pt_rules 同级导入）。

运行：python -m pytest tests/test_pt_upload.py -v
"""

import hashlib
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import pt_upload  # noqa: E402


# --------------------------------------------------------------------------
# content_md5
# --------------------------------------------------------------------------

class TestContentMd5:
    def test_md5_matches_known_bytes(self, tmp_path):
        data = b"hello optical power toolkit"
        f = tmp_path / "x.xlsx"
        f.write_bytes(data)
        expect = hashlib.md5(data).hexdigest()
        assert pt_upload.content_md5(str(f)) == expect

    def test_md5_is_32_hex_lower(self, tmp_path):
        f = tmp_path / "x.xlsx"
        f.write_bytes(b"abc")
        md5 = pt_upload.content_md5(str(f))
        assert len(md5) == 32
        assert md5 == md5.lower()
        int(md5, 16)  # 全 hex，非法则抛 ValueError


# --------------------------------------------------------------------------
# build_upload_records —— junk 过滤 / 真盒子保留
# --------------------------------------------------------------------------

class TestBuildUploadRecords:
    def _write_rows(self, tmp_path, rows):
        rj = tmp_path / "rows.json"
        rj.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        ocr = tmp_path / "ocr"
        ocr.mkdir()
        return str(rj), str(ocr)

    def test_junk_row_filtered_real_kept(self, tmp_path):
        rows = [
            {"source_file": "f.xlsx", "sheet": "S1", "row": 2,
             "box_table": "H1L1S1", "cluster": "C", "power_table": "20.5",
             "passfail_table": "PASS", "has_photo": False},
            {"source_file": "f.xlsx", "sheet": "S1", "row": 3,
             "box_table": "not-a-box", "cluster": "C", "has_photo": False},
        ]
        rj, ocr = self._write_rows(tmp_path, rows)
        recs = pt_upload.build_upload_records(rj, ocr, "gpt")
        # junk（无 H##L#S# 后缀）被过滤，只剩真盒子
        assert len(recs) == 1
        assert recs[0]["box_name"] == "H1L1S1"
        assert recs[0]["sheet"] == "S1"
        assert recs[0]["row"] == 2

    def test_all_junk_yields_empty(self, tmp_path):
        rows = [
            {"source_file": "f.xlsx", "sheet": "S1", "row": 1,
             "box_table": "junk", "has_photo": False},
        ]
        rj, ocr = self._write_rows(tmp_path, rows)
        assert pt_upload.build_upload_records(rj, ocr, "gpt") == []


# --------------------------------------------------------------------------
# collect_images —— rec["image"] 字段
# --------------------------------------------------------------------------

class TestCollectImages:
    def test_collects_existing_image_by_sheet_row(self, tmp_path):
        img = tmp_path / "pic.jpg"
        img.write_bytes(b"\xff\xd8jpeg")
        rows = [{"sheet": "S1", "row": 5, "has_photo": True, "image": str(img)}]
        rj = tmp_path / "rows.json"
        rj.write_text(json.dumps(rows), encoding="utf-8")
        out = pt_upload.collect_images(str(rj))
        assert out == {("S1", 5): str(img)}

    def test_skips_when_has_photo_false(self, tmp_path):
        img = tmp_path / "pic.jpg"
        img.write_bytes(b"\xff\xd8jpeg")
        rows = [{"sheet": "S1", "row": 5, "has_photo": False, "image": str(img)}]
        rj = tmp_path / "rows.json"
        rj.write_text(json.dumps(rows), encoding="utf-8")
        assert pt_upload.collect_images(str(rj)) == {}

    def test_skips_when_image_file_missing(self, tmp_path):
        rows = [{"sheet": "S1", "row": 5, "has_photo": True,
                 "image": str(tmp_path / "gone.jpg")}]
        rj = tmp_path / "rows.json"
        rj.write_text(json.dumps(rows), encoding="utf-8")
        assert pt_upload.collect_images(str(rj)) == {}

    def test_skips_when_image_field_absent(self, tmp_path):
        rows = [{"sheet": "S1", "row": 5, "has_photo": True}]
        rj = tmp_path / "rows.json"
        rj.write_text(json.dumps(rows), encoding="utf-8")
        assert pt_upload.collect_images(str(rj)) == {}
