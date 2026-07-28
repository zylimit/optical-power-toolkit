"""光功率工具箱 V2.0 上传路由：POST /files/{source_file}/records + GET /files。

职责边界：本模块只做 multipart 请求解析 + 响应组装 + 上传业务编排（去重三分支、
存图/存记录的顺序与事务）。DB 读写下沉 server/db.py（upsert_record / upsert_file /
get_file_by_md5_size / list_files），图落盘下沉 server/imagestore.py。

图 ↔ records 关联约定
--------------------
客户端以三个并列 multipart 字段传图（三者按下标对齐，长度必须一致）：
- images：图二进制文件列表（UploadFile[]）
- image_sheets：各图对应的 sheet 字符串列表（Form 多值）
- image_rows：各图对应的 row 整数列表（Form 多值）
即第 i 张图属于 (image_sheets[i], image_rows[i])。选并列列表而非 img_{sheet}_{row}
动态字段名，是因为 sheet 可含任意字符（空格/中文/符号），塞进字段名需再清洗、
易与解析歧义；并列列表下标对齐无歧义，且 sheet 原值不失真。
无图上传时三字段可全部省略。

content_md5 白名单（接口层，严于 imagestore 黑名单）
--------------------------------------------------
入口校验 content_md5 匹配 md5 摘要格式 ^[0-9a-fA-F]{32}$，非法直接 400，不进
imagestore。imagestore 的 [/\\:]|\\.\\. 黑名单是库层兜底（defense-in-depth）。
"""

import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, UploadFile

from server import imagestore
from server.db import (
    connect,
    get_file_by_md5_size,
    list_files,
    upsert_file,
    upsert_record,
)

router = APIRouter()

# md5 摘要白名单：32 位十六进制。接口层挡在 imagestore 之前。
_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")

# DB 路径：与 imagestore.IMAGES_DIR 同级，相对模块定位不依赖 cwd。
# 覆写点：测试冒烟通过环境变量 OPT_DB_PATH 指定独立库，避免污染主库。
import os

_DEFAULT_DB = str(imagestore.IMAGES_DIR.parent / "toolkit.db")
DB_PATH: str = os.environ.get("OPT_DB_PATH", _DEFAULT_DB)


def _now_iso() -> str:
    """服务端处理时间戳（UTC ISO8601），作 files.processed_at。"""
    return datetime.now(timezone.utc).isoformat()


@router.post("/files/{source_file}/records")
async def upload_records(
    source_file: str,
    content_md5: str = Form(...),
    file_size: int = Form(...),
    records: str = Form(...),
    model: str | None = Form(None),
    images: list[UploadFile] | None = None,
    image_sheets: list[str] | None = Form(None),
    image_rows: list[int] | None = Form(None),
) -> dict:
    """接收一批 records + 原图，按文件去重三分支入库。

    去重（content_md5 + file_size 双校验）：
    - 无同内容文件           → 新入库（存图 + 存记录 + files 插一条，duplicate_of=None）
    - 同 source_file 同内容  → records upsert 覆盖（幂等），图照存（同 md5 路径复用即覆盖）
    - 不同 source_file 同内容 → files 标 duplicate_of=已存在文件、records 照常入库、图不重存

    返回 {source_file, records_stored, images_stored, duplicate_of}。
    """
    # ① content_md5 白名单（严于 imagestore 黑名单），非法 400
    if not _MD5_RE.match(content_md5):
        raise HTTPException(status_code=400, detail=f"非法 content_md5: {content_md5!r}")

    # ② 解析 records JSON
    try:
        rec_list = json.loads(records)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"records 不是合法 JSON: {e}")
    if not isinstance(rec_list, list):
        raise HTTPException(status_code=400, detail="records 必须为 JSON 数组")

    # ③ 图三字段对齐校验
    imgs = images or []
    sheets = image_sheets or []
    rows = image_rows or []
    if not (len(imgs) == len(sheets) == len(rows)):
        raise HTTPException(
            status_code=400,
            detail=f"images/image_sheets/image_rows 长度不一致: "
            f"{len(imgs)}/{len(sheets)}/{len(rows)}",
        )

    conn = connect(DB_PATH)
    try:
        # ④ 去重判定：找有无同 content_md5+file_size 的非重复源文件
        existing = get_file_by_md5_size(conn, content_md5, file_size)
        duplicate_of = existing if (existing and existing != source_file) else None
        reuse_images = duplicate_of is not None  # 跨文件同内容 → 图复用不重存

        # ⑤ 存图（reuse 时跳过写盘，仅复用已有 content_md5 路径）+ 记录 image_path
        # image_path 按 (sheet,row) 索引，供 records 写入
        image_paths: dict[tuple[str, int], str] = {}
        images_stored = 0
        for up, sheet, row in zip(imgs, sheets, rows):
            data = await up.read()
            path = imagestore._build_path(content_md5, sheet, row)  # 先校验路径（库层黑名单兜底）
            if reuse_images and imagestore.exists(content_md5, sheet, row):
                # 图已存在于同 md5 路径下，复用不重写
                image_paths[(sheet, row)] = str(path)
            else:
                saved = imagestore.save_image(content_md5, sheet, row, data)
                image_paths[(sheet, row)] = str(saved)
                images_stored += 1

        # ⑥ upsert records，回填 image_path（该 sheet+row 有图才写）
        records_stored = 0
        sheet_set: set[str] = set()
        for rec in rec_list:
            if not isinstance(rec, dict):
                raise HTTPException(status_code=400, detail="records 每项必须为对象")
            sheet = rec.get("sheet")
            row = rec.get("row")
            key = (sheet, row)
            if key in image_paths:
                rec = {**rec, "image_path": image_paths[key]}
            upsert_record(conn, source_file, rec)
            records_stored += 1
            if sheet is not None:
                sheet_set.add(sheet)

        # ⑦ files 表 upsert（record_count 统计本批，processed_at 服务端时间戳）
        upsert_file(
            conn,
            {
                "file_name": source_file,
                "file_size": file_size,
                "sheets": len(sheet_set),
                "rows": records_stored,
                "photos": len(image_paths),
                "record_count": records_stored,
                "model": model,
                "processed_at": _now_iso(),
                "content_md5": content_md5,
                "duplicate_of": duplicate_of,
            },
        )

        conn.commit()  # 事务：全部成功才落盘，中途抛错回滚
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "source_file": source_file,
        "records_stored": records_stored,
        "images_stored": images_stored,
        "duplicate_of": duplicate_of,
    }


@router.get("/files")
def get_files(since: str | None = None) -> list[dict]:
    """返回文件清单，供客户端上传前查增量跳过。

    [{source_file, content_md5, file_size, record_count, duplicate_of, ocr_at}]
    since 非空时过滤 processed_at > since（ISO / unix-ts 串，字典序比较）。
    """
    conn = connect(DB_PATH)
    try:
        return list_files(conn, since)
    finally:
        conn.close()
