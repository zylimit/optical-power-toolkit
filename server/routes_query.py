"""光功率工具箱 V2.0 查询路由：GET /records、/records/{id}、/records/{id}/image。

职责边界：本模块只做查询参数解析 + 响应组装 + 404 判定。合并去重 SQL 与
DB 访问下沉 server/query_service.py，取图路径为 records.image_path（原图绝对路径）。

DB 路径约定与 routes_upload.py 一致：默认 imagestore.IMAGES_DIR 同级 toolkit.db，
可用环境变量 OPT_DB_PATH 覆写（冒烟测试指向独立临时库，避免污染主库）。
"""

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from server import imagestore
from server.db import connect
from server.query_service import (
    get_image_path_by_id,
    get_record_by_id,
    query_records,
)

router = APIRouter()

_DEFAULT_DB = str(imagestore.IMAGES_DIR.parent / "toolkit.db")
DB_PATH: str = os.environ.get("OPT_DB_PATH", _DEFAULT_DB)


@router.get("/records")
def list_records(
    area: str | None = Query(None),
    zone: str | None = Query(None),
    hub: str | None = Query(None),
    box_name: str | None = Query(None),
    needs_retest: str | None = Query(None),
    photo_status: str | None = Query(None),
    conf: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1),
    include_duplicates: bool = Query(False),
    include_orphans: bool = Query(False),
) -> dict:
    """合并去重后的赢家记录分页查询。

    记录合并在查询时用窗口函数算（不物化）：同 merge_key 选 evidence 高、
    conf 高、ocr_at 新的赢家。默认排除重复副本与孤儿，开关放开。
    返回 {total, page, page_size, records:[赢家全字段]}。
    """
    filters = {
        "area": area,
        "zone": zone,
        "hub": hub,
        "box_name": box_name,
        "needs_retest": needs_retest,
        "photo_status": photo_status,
        "conf": conf,
    }
    conn = connect(DB_PATH)
    try:
        return query_records(
            conn,
            filters,
            page=page,
            page_size=page_size,
            include_duplicates=include_duplicates,
            include_orphans=include_orphans,
        )
    finally:
        conn.close()


@router.get("/records/{record_id}")
def get_record(record_id: int) -> dict:
    """按 records.id 返回单条详情（全字段）。不存在 → 404。"""
    conn = connect(DB_PATH)
    try:
        rec = get_record_by_id(conn, record_id)
    finally:
        conn.close()
    if rec is None:
        raise HTTPException(status_code=404, detail=f"记录不存在: id={record_id}")
    return rec


@router.get("/records/{record_id}/image")
def get_record_image(record_id: int) -> FileResponse:
    """按 records.id 取该记录 image_path 指向的原图，返回 image/jpeg。

    记录不存在、image_path 空/NULL、或文件不存在 → 404。
    """
    conn = connect(DB_PATH)
    try:
        image_path = get_image_path_by_id(conn, record_id)
    finally:
        conn.close()
    if not image_path or not Path(image_path).exists():
        raise HTTPException(status_code=404, detail=f"图片不存在: id={record_id}")
    return FileResponse(image_path, media_type="image/jpeg")
