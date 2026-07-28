# -*- coding: utf-8 -*-
"""server 上传路由 POST /files/{source_file}/records + GET /files —— 回归测试。

覆盖高价值契约：
- content_md5 白名单（非 32 位 hex → 400）
- 文件去重三分支：新入库 / 同 source_file 幂等覆盖 / 跨 source_file 标 duplicate_of + 图不重存
- 图三并列字段（images/image_sheets/image_rows）长度校验
- GET /files 清单 + since 过滤

隔离：每测用独立临时 DB（monkeypatch routes_upload.DB_PATH / routes_query.DB_PATH
指向 tmp_path 下 .db）+ monkeypatch imagestore.IMAGES_DIR 到 tmp_path。
routes 模块级 DB_PATH 常量在请求处理时才 connect(DB_PATH)，故 setattr 即生效，
不污染 server/images、不留 server/*.db。

运行：python -m pytest tests/test_server_upload.py -v
"""

import json

import pytest
from fastapi.testclient import TestClient

from server import imagestore, routes_query, routes_upload
from server.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """独立临时库 + 临时图目录的 TestClient。"""
    db = str(tmp_path / "test.db")
    monkeypatch.setattr(routes_upload, "DB_PATH", db)
    monkeypatch.setattr(routes_query, "DB_PATH", db)
    monkeypatch.setattr(imagestore, "IMAGES_DIR", tmp_path / "images")
    return TestClient(app)


# 32 位合法 md5（内容随意，只要形状对）
MD5_A = "a" * 32
MD5_B = "b" * 32


def _recs(box="H1L1S1", sheet="Sheet1", row=1):
    """一条最小 record（服务端只按白名单取列，未知键忽略）。"""
    return [{"sheet": sheet, "row": row, "box_name": box, "power_dbm": 20.0,
             "photo_status": "无图", "conf": "高"}]


def _post(client, source_file, md5, size, recs, files=None, extra=None):
    data = {"content_md5": md5, "file_size": str(size),
            "records": json.dumps(recs, ensure_ascii=False)}
    if extra:
        data.update(extra)
    return client.post(f"/files/{source_file}/records", data=data, files=files)


# --------------------------------------------------------------------------
# content_md5 白名单
# --------------------------------------------------------------------------

class TestMd5Whitelist:
    def test_bad_md5_too_short_returns_400(self, client):
        r = _post(client, "f.xlsx", "abc", 100, _recs())
        assert r.status_code == 400

    def test_bad_md5_non_hex_returns_400(self, client):
        r = _post(client, "f.xlsx", "z" * 32, 100, _recs())
        assert r.status_code == 400

    def test_valid_md5_ok(self, client):
        r = _post(client, "f.xlsx", MD5_A, 100, _recs())
        assert r.status_code == 200


# --------------------------------------------------------------------------
# 文件去重三分支
# --------------------------------------------------------------------------

class TestDedup:
    def test_new_file_stored(self, client):
        r = _post(client, "new.xlsx", MD5_A, 100, _recs())
        assert r.status_code == 200
        body = r.json()
        assert body["source_file"] == "new.xlsx"
        assert body["records_stored"] == 1
        assert body["duplicate_of"] is None

    def test_same_source_file_idempotent_overwrite(self, client):
        # 同 source_file 同内容再传一次 → 覆盖，不算 duplicate
        _post(client, "same.xlsx", MD5_A, 100, _recs(box="H1L1S1"))
        r = _post(client, "same.xlsx", MD5_A, 100, _recs(box="H1L1S1"))
        assert r.status_code == 200
        assert r.json()["duplicate_of"] is None
        # 同唯一键 (source_file,sheet,row) upsert 覆盖 → 只有 1 条记录
        files = client.get("/files").json()
        same = [f for f in files if f["source_file"] == "same.xlsx"]
        assert len(same) == 1
        assert same[0]["record_count"] == 1

    def test_cross_source_file_marks_duplicate_of(self, client):
        _post(client, "orig.xlsx", MD5_A, 100, _recs())
        r = _post(client, "copy.xlsx", MD5_A, 100, _recs())
        assert r.status_code == 200
        assert r.json()["duplicate_of"] == "orig.xlsx"

    def test_cross_source_file_images_not_restored(self, client):
        # 原文件带图上传 → images_stored=1；跨 source_file 同内容再传 → 图复用不重存 images_stored=0
        img = ("images", ("a.jpg", b"\xff\xd8jpeg", "image/jpeg"))
        extra = {"image_sheets": "Sheet1", "image_rows": "1"}
        r1 = _post(client, "orig2.xlsx", MD5_B, 200, _recs(), files=[img], extra=extra)
        assert r1.status_code == 200
        assert r1.json()["images_stored"] == 1
        img2 = ("images", ("a.jpg", b"\xff\xd8jpeg", "image/jpeg"))
        r2 = _post(client, "copy2.xlsx", MD5_B, 200, _recs(), files=[img2], extra=extra)
        assert r2.status_code == 200
        assert r2.json()["duplicate_of"] == "orig2.xlsx"
        assert r2.json()["images_stored"] == 0


# --------------------------------------------------------------------------
# 图三并列字段长度校验
# --------------------------------------------------------------------------

class TestImageAlignment:
    def test_length_mismatch_returns_400(self, client):
        # 传 1 张图但缺 image_sheets/image_rows → 长度不一致 → 400
        img = ("images", ("a.jpg", b"\xff\xd8jpeg", "image/jpeg"))
        r = _post(client, "f.xlsx", MD5_A, 100, _recs(), files=[img])
        assert r.status_code == 400


# --------------------------------------------------------------------------
# GET /files + since 过滤
# --------------------------------------------------------------------------

class TestListFiles:
    def test_list_files_shape(self, client):
        _post(client, "f1.xlsx", MD5_A, 100, _recs())
        files = client.get("/files").json()
        assert len(files) == 1
        f = files[0]
        for k in ("source_file", "content_md5", "file_size",
                  "record_count", "duplicate_of", "ocr_at"):
            assert k in f
        assert f["source_file"] == "f1.xlsx"
        assert f["content_md5"] == MD5_A
        assert f["file_size"] == 100

    def test_since_filter_excludes_old(self, client):
        _post(client, "f1.xlsx", MD5_A, 100, _recs())
        # since 取一个远未来时间戳 → processed_at > since 全不满足 → 空
        got = client.get("/files", params={"since": "9999-01-01T00:00:00"}).json()
        assert got == []

    def test_since_filter_includes_new(self, client):
        _post(client, "f1.xlsx", MD5_A, 100, _recs())
        # since 取远古时间 → 全部满足
        got = client.get("/files", params={"since": "1970-01-01T00:00:00"}).json()
        assert len(got) == 1


# --------------------------------------------------------------------------
# red-locks：已确认缺陷的失败测试（锁红，待 implementer 修绿）
# --------------------------------------------------------------------------

class TestRedlockRecordCountMultiBatch:
    """缺陷1：record_count 多批上传只记最后一批数。

    Spec 契约：GET /files 的 record_count 应为该文件的总记录数。
    客户端 pt_upload 按 BATCH_SIZE=50 分批 POST，>50 行文件会分多批。
    现状：upsert_file 每批把 record_count 覆盖为本批 records_stored，
    导致 GET /files 返回最后一批数而非总数。红测试锁该缺陷。
    """

    def test_record_count_after_multibatch(self, client):
        # 60 条记录，每批 30 条（BATCH_SIZE 实际是 50，这里用 30 拆 2 批以减少构造量；
        # 关键是「分多批」这一行为本身）
        batch1 = _recs() + [_recs(row=2), _recs(row=3)]  # 3 条不够，构造 30 条
        rows_batch1 = [{"sheet": "Sheet1", "row": i, "box_name": "H1L1S1",
                        "power_dbm": 20.0, "photo_status": "无图", "conf": "高"}
                       for i in range(1, 31)]
        rows_batch2 = [{"sheet": "Sheet1", "row": i, "box_name": "H1L1S1",
                        "power_dbm": 20.0, "photo_status": "无图", "conf": "高"}
                       for i in range(31, 61)]
        # 同 source_file + 同 content_md5 + 同 file_size → 第二批走同 source_file 幂等覆盖分支
        r1 = _post(client, "big.xlsx", MD5_A, 600, rows_batch1)
        assert r1.status_code == 200
        r2 = _post(client, "big.xlsx", MD5_A, 600, rows_batch2)
        assert r2.status_code == 200
        files = client.get("/files").json()
        big = [f for f in files if f["source_file"] == "big.xlsx"]
        assert len(big) == 1
        # 期望 60（文件总记录数），现状为 30（最后一批数）→ 红
        assert big[0]["record_count"] == 60, (
            f"record_count 多批上传应=文件总记录数 60，实际={big[0]['record_count']}"
            "（缺陷1：upsert_file 每批覆盖 record_count 为本批数）"
        )


class TestRedlockOldImageDeletedOnMd5Change:
    """缺陷2：content_md5 变更覆盖旧图悬空。

    DEV-PLAN 契约：「无悬空旧图」——同 source_file 重传且内容变（md5 变）时，
    旧 md5 目录下的图应删除，新图落新 md5 目录。现状：旧图残留。
    """

    def test_old_image_deleted_on_md5_change(self, client, tmp_path):
        from server import imagestore as _img
        # 第一次：fileA md5=aaa + 1 图（sheet=Sheet1, row=1）
        img1 = ("images", ("a.jpg", b"\xff\xd8jpeg-A", "image/jpeg"))
        extra1 = {"image_sheets": "Sheet1", "image_rows": "1"}
        r1 = _post(client, "fileA.xlsx", MD5_A, 100, _recs(),
                   files=[img1], extra=extra1)
        assert r1.status_code == 200
        old_dir = _img.IMAGES_DIR / MD5_A
        assert old_dir.exists(), "前置：旧 md5 图目录应存在"
        # 第二次：同 source_file，md5 变为 bbb（内容变）+ 1 图（同 sheet/row）
        img2 = ("images", ("a.jpg", b"\xff\xd8jpeg-B", "image/jpeg"))
        extra2 = {"image_sheets": "Sheet1", "image_rows": "1"}
        r2 = _post(client, "fileA.xlsx", MD5_B, 100, _recs(),
                   files=[img2], extra=extra2)
        assert r2.status_code == 200
        # 断言：旧 md5 目录应已被删除（无悬空旧图），现状残留 → 红
        assert not old_dir.exists(), (
            f"旧 md5 目录 {old_dir} 应在重传 md5 变更后删除（无悬空旧图），"
            f"实际仍存在（缺陷2：md5 变更未清理旧图）"
        )
        # 新 md5 目录应有图
        new_dir = _img.IMAGES_DIR / MD5_B
        assert new_dir.exists(), "新 md5 图目录应存在"
