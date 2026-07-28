"""V2.0 服务化：把 rows.json + ocr 目录合并出的 records + 原图上传到服务端。

落库出口从本地 SQLite（pt_db.store）改为 HTTP POST 到 server。合并规则完全
复用 pt_db.build_records（同一套 reconcile/派生列逻辑，不重写——V1 踩过很多坑），
只是终点从 sqlite 换成 multipart 上传。

图 ↔ records 关联走服务端约定的三并列字段：images（文件）+ image_sheets +
image_rows，三者按下标对齐。records 的 sheet/row 与图的 sheet/row 对上，服务端
回填 image_path。

    from pt_upload import ServerClient, build_upload_records
    client = ServerClient("http://localhost:8000")
    done = client.list_done_md5()          # 增量：已入库的 content_md5 集合
    recs = build_upload_records(rows_json, ocr_dir, model)
    imgs = collect_images(rows_json)
    client.upload(name, content_md5, file_size, recs, imgs)
"""

import hashlib
import json
import os
import sys

import requests

import pt_db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BATCH_SIZE = 50   # 每批最多几行/图：与服务端一批 upsert 对齐，控制单请求体积
UPLOAD_TIMEOUT = 300


def content_md5(path):
    """对整个 xlsx 文件算 md5，返回 32 位小写十六进制（服务端白名单格式）。"""
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def build_upload_records(rows_json, ocr_dir, model):
    """合并 rows.json + ocr 目录成 records 列表（复用 pt_db.build_records，
    列同 REC_COLS，含 sheet/row/box_name/power_dbm/area/hub/level/fat 等全字段
    + 派生列）。终点是上传，不落库。"""
    import time
    rows = json.load(open(rows_json, encoding="utf-8"))
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    return pt_db.build_records(rows, ocr_dir, model, ts)


def collect_images(rows_json):
    """从 rows.json 收集有图记录的原图路径，按 (sheet, row) 索引。

    原图是 pt_extract 抽到临时目录 images/ 下的照片（rec["image"]）。只收
    has_photo 且文件真实存在的；build_records 只保留真盒子行（H##L#S# 后缀），
    上传时以 records 的 (sheet,row) 为准挑图，这里全收、由调用方按 records 过滤。
    """
    rows = json.load(open(rows_json, encoding="utf-8"))
    out = {}
    for r in rows:
        if not r.get("has_photo"):
            continue
        img = r.get("image")
        if img and os.path.exists(img):
            out[(r.get("sheet"), r["row"])] = img
    return out


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


class ServerClient:
    """服务端上传/查询薄封装。"""

    def __init__(self, base_url):
        self.base = base_url.rstrip("/")

    def list_files(self):
        """GET /files → [{source_file, content_md5, file_size, ...}]。"""
        r = requests.get(f"{self.base}/files", timeout=60)
        r.raise_for_status()
        return r.json()

    def list_done_md5(self):
        """已入库文件的 content_md5 集合（增量跳过用）。"""
        return {f.get("content_md5") for f in self.list_files() if f.get("content_md5")}

    def upload(self, source_file, md5, file_size, records, image_map, model=None):
        """分批 POST records + 图到 /files/{source_file}/records。

        每批 ≤ BATCH_SIZE 行；records 作 JSON 字符串字段，图走 images +
        image_sheets + image_rows 三并列（下标对齐）。任一批非 200 抛异常，
        由调用方按文件计 fail，不静默吞。返回累计 {records_stored, images_stored}。
        """
        totals = {"records_stored": 0, "images_stored": 0, "duplicate_of": None}
        for batch in _chunks(records, BATCH_SIZE):
            data = [
                ("content_md5", md5),
                ("file_size", str(file_size)),
                ("records", json.dumps(batch, ensure_ascii=False)),
            ]
            if model:
                data.append(("model", model))
            files = []
            opened = []
            for rec in batch:
                key = (rec.get("sheet"), rec.get("row"))
                img = image_map.get(key)
                if not img:
                    continue
                data.append(("image_sheets", "" if key[0] is None else str(key[0])))
                data.append(("image_rows", str(key[1])))
                fh = open(img, "rb")
                opened.append(fh)
                files.append(("images", (os.path.basename(img), fh, "image/jpeg")))
            try:
                r = requests.post(
                    f"{self.base}/files/{source_file}/records",
                    data=data, files=files or None, timeout=UPLOAD_TIMEOUT)
            finally:
                for fh in opened:
                    fh.close()
            if r.status_code != 200:
                raise RuntimeError(
                    f"上传失败 HTTP {r.status_code}: {r.text[:200]}")
            body = r.json()
            totals["records_stored"] += body.get("records_stored", 0)
            totals["images_stored"] += body.get("images_stored", 0)
            totals["duplicate_of"] = body.get("duplicate_of")
        return totals
