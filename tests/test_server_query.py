# -*- coding: utf-8 -*-
"""server 查询路由 GET /records、/records/{id}、/records/{id}/image —— 回归测试。

覆盖记录级合并去重的核心契约（query_service._MERGE_CTE）：
- 合并选赢家：同 merge_key（L1 层级键或 L3 box_name 键）选 evidence DESC
  → conf_rank ASC → ocr_at DESC 的 rn=1。核心用例：同 box 两条「有图清晰」vs
  「无图」，断言只返回有图清晰赢家。
- evidence 放宽：photo_status='有图清晰' 即 evidence=1（不看 power_check）——
  即使 power_check 不符，有图清晰仍赢无图。
- duplicate_of 非空默认排除 / include_duplicates 放开。
- 孤儿（merge_key IS NULL：L1 不全且 box_name 空）默认排除 / include_orphans 放开。
- 筛选 area/zone/hub/box_name + 分页 page/page_size。
- 单条 /records/{id}：命中 200 / 不存在 404。
- 取图 /records/{id}/image：有图 200 image/jpeg / 无图或文件缺失 404。

隔离：每测独立临时库（monkeypatch routes_query.DB_PATH），直接用 server.db
写入受控数据（绕 HTTP，精确造 merge_key/photo_status/duplicate_of），再经
TestClient 查询。图落 tmp_path，不污染 server/images、不留 server/*.db。

运行：python -m pytest tests/test_server_query.py -v
"""

import pytest
from fastapi.testclient import TestClient

from server import db as server_db
from server import routes_query, routes_upload
from server.app import app


@pytest.fixture
def env(tmp_path, monkeypatch):
    """独立临时库；返回 (client, db_path, tmp_path)。"""
    db_path = str(tmp_path / "q.db")
    monkeypatch.setattr(routes_query, "DB_PATH", db_path)
    monkeypatch.setattr(routes_upload, "DB_PATH", db_path)
    client = TestClient(app)
    return client, db_path, tmp_path


def _insert_file(conn, name, duplicate_of=None):
    server_db.upsert_file(conn, {
        "file_name": name, "file_size": 1, "sheets": 1, "rows": 1, "photos": 0,
        "record_count": 1, "model": None, "processed_at": "2025-01-01T00:00:00",
        "content_md5": "a" * 32, "duplicate_of": duplicate_of,
    })


def _insert_rec(conn, source_file, sheet, row, **kw):
    """写一条 record；用 L1 层级键字段（area/hub/level/fat）默认造非孤儿。"""
    rec = {
        "sheet": sheet, "row": row,
        "box_name": kw.get("box_name", "BOX1"),
        "area": kw.get("area", "A1"), "cluster_code": kw.get("cluster_code", "C1"),
        "zone": kw.get("zone", "Z1"), "hub": kw.get("hub", "H1"),
        "level": kw.get("level", "L1"), "fat": kw.get("fat", "S1"),
        "photo_status": kw.get("photo_status", "无图"),
        "power_check": kw.get("power_check"),
        "conf": kw.get("conf", "高"),
        "ocr_at": kw.get("ocr_at", "2025-01-01 00:00:00"),
        "image_path": kw.get("image_path"),
    }
    server_db.upsert_record(conn, source_file, rec)


# --------------------------------------------------------------------------
# 合并选赢家：有图清晰 vs 无图
# --------------------------------------------------------------------------

class TestWinnerSelection:
    def test_photo_clear_beats_no_photo(self, env):
        client, db_path, _ = env
        conn = server_db.connect(db_path)
        _insert_file(conn, "f.xlsx")
        # 同 L1 merge_key（同 area/hub/level/fat/cluster/zone），一条有图清晰一条无图
        _insert_rec(conn, "f.xlsx", "S", 1, photo_status="无图", conf="高")
        _insert_rec(conn, "f.xlsx", "S", 2, photo_status="有图清晰", conf="低")
        conn.commit(); conn.close()
        body = client.get("/records").json()
        assert body["total"] == 1
        assert body["records"][0]["photo_status"] == "有图清晰"

    def test_evidence_relaxed_ignores_power_check(self, env):
        # 有图清晰即便 power_check='表图不符'，仍赢无图（evidence 不看 power_check）
        client, db_path, _ = env
        conn = server_db.connect(db_path)
        _insert_file(conn, "f.xlsx")
        _insert_rec(conn, "f.xlsx", "S", 1, photo_status="无图", conf="高",
                    power_check="一致")
        _insert_rec(conn, "f.xlsx", "S", 2, photo_status="有图清晰", conf="低",
                    power_check="表图不符")
        conn.commit(); conn.close()
        body = client.get("/records").json()
        assert body["total"] == 1
        assert body["records"][0]["photo_status"] == "有图清晰"
        assert body["records"][0]["power_check"] == "表图不符"


# --------------------------------------------------------------------------
# duplicate 默认排除 / include 放开
# --------------------------------------------------------------------------

class TestDuplicates:
    def test_duplicate_excluded_by_default(self, env):
        client, db_path, _ = env
        conn = server_db.connect(db_path)
        _insert_file(conn, "dup.xlsx", duplicate_of="orig.xlsx")
        _insert_rec(conn, "dup.xlsx", "S", 1)
        conn.commit(); conn.close()
        assert client.get("/records").json()["total"] == 0

    def test_duplicate_included_when_flag_set(self, env):
        client, db_path, _ = env
        conn = server_db.connect(db_path)
        _insert_file(conn, "dup.xlsx", duplicate_of="orig.xlsx")
        _insert_rec(conn, "dup.xlsx", "S", 1)
        conn.commit(); conn.close()
        got = client.get("/records", params={"include_duplicates": 1}).json()
        assert got["total"] == 1


# --------------------------------------------------------------------------
# 孤儿默认排除 / include 放开
# --------------------------------------------------------------------------

class TestOrphans:
    def _insert_orphan(self, conn, source_file):
        # L1 不全（无 hub/level/fat）且 box_name 空 → merge_key NULL → 孤儿
        server_db.upsert_record(conn, source_file, {
            "sheet": "S", "row": 1, "box_name": "",
            "area": None, "hub": None, "level": None, "fat": None,
            "photo_status": "无图", "conf": "高", "ocr_at": "2025-01-01 00:00:00",
        })

    def test_orphan_excluded_by_default(self, env):
        client, db_path, _ = env
        conn = server_db.connect(db_path)
        _insert_file(conn, "o.xlsx")
        self._insert_orphan(conn, "o.xlsx")
        conn.commit(); conn.close()
        assert client.get("/records").json()["total"] == 0

    def test_orphan_included_when_flag_set(self, env):
        client, db_path, _ = env
        conn = server_db.connect(db_path)
        _insert_file(conn, "o.xlsx")
        self._insert_orphan(conn, "o.xlsx")
        conn.commit(); conn.close()
        got = client.get("/records", params={"include_orphans": 1}).json()
        assert got["total"] == 1


# --------------------------------------------------------------------------
# 筛选 + 分页
# --------------------------------------------------------------------------

class TestFilterAndPage:
    def _seed_distinct(self, db_path):
        conn = server_db.connect(db_path)
        _insert_file(conn, "f.xlsx")
        # 三条各自不同 merge_key（不同 hub），各成赢家
        _insert_rec(conn, "f.xlsx", "S", 1, hub="H1", zone="Z1")
        _insert_rec(conn, "f.xlsx", "S", 2, hub="H2", zone="Z2")
        _insert_rec(conn, "f.xlsx", "S", 3, hub="H3", zone="Z2")
        conn.commit(); conn.close()

    def test_filter_by_hub(self, env):
        client, db_path, _ = env
        self._seed_distinct(db_path)
        got = client.get("/records", params={"hub": "H2"}).json()
        assert got["total"] == 1
        assert got["records"][0]["hub"] == "H2"

    def test_filter_by_zone(self, env):
        client, db_path, _ = env
        self._seed_distinct(db_path)
        got = client.get("/records", params={"zone": "Z2"}).json()
        assert got["total"] == 2

    def test_pagination(self, env):
        client, db_path, _ = env
        self._seed_distinct(db_path)
        p1 = client.get("/records", params={"page": 1, "page_size": 2}).json()
        assert p1["total"] == 3
        assert len(p1["records"]) == 2
        p2 = client.get("/records", params={"page": 2, "page_size": 2}).json()
        assert len(p2["records"]) == 1


# --------------------------------------------------------------------------
# 单条 /records/{id}
# --------------------------------------------------------------------------

class TestGetOne:
    def test_get_existing_record(self, env):
        client, db_path, _ = env
        conn = server_db.connect(db_path)
        _insert_file(conn, "f.xlsx")
        _insert_rec(conn, "f.xlsx", "S", 1, box_name="BOXX")
        rid = conn.execute("SELECT id FROM records").fetchone()[0]
        conn.commit(); conn.close()
        r = client.get(f"/records/{rid}")
        assert r.status_code == 200
        assert r.json()["box_name"] == "BOXX"

    def test_get_missing_record_404(self, env):
        client, _, _ = env
        assert client.get("/records/999999").status_code == 404


# --------------------------------------------------------------------------
# 取图 /records/{id}/image
# --------------------------------------------------------------------------

class TestGetImage:
    def test_image_200(self, env):
        client, db_path, tmp_path = env
        img = tmp_path / "pic.jpg"
        img.write_bytes(b"\xff\xd8jpegbytes")
        conn = server_db.connect(db_path)
        _insert_file(conn, "f.xlsx")
        _insert_rec(conn, "f.xlsx", "S", 1, image_path=str(img))
        rid = conn.execute("SELECT id FROM records").fetchone()[0]
        conn.commit(); conn.close()
        r = client.get(f"/records/{rid}/image")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/jpeg")
        assert r.content == b"\xff\xd8jpegbytes"

    def test_image_404_when_no_image_path(self, env):
        client, db_path, _ = env
        conn = server_db.connect(db_path)
        _insert_file(conn, "f.xlsx")
        _insert_rec(conn, "f.xlsx", "S", 1, image_path=None)
        rid = conn.execute("SELECT id FROM records").fetchone()[0]
        conn.commit(); conn.close()
        assert client.get(f"/records/{rid}/image").status_code == 404

    def test_image_404_when_file_missing(self, env):
        client, db_path, tmp_path = env
        conn = server_db.connect(db_path)
        _insert_file(conn, "f.xlsx")
        _insert_rec(conn, "f.xlsx", "S", 1, image_path=str(tmp_path / "gone.jpg"))
        rid = conn.execute("SELECT id FROM records").fetchone()[0]
        conn.commit(); conn.close()
        assert client.get(f"/records/{rid}/image").status_code == 404

    def test_image_404_record_missing(self, env):
        client, _, _ = env
        assert client.get("/records/999999/image").status_code == 404
