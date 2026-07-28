"""光功率工具箱 V2.0 服务端入口。

FastAPI 实例 + 健康检查路由；后续 Phase 5/6 路由在此 app 上 include_router。
"""

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI

from server.routes_upload import router as upload_router
from server.routes_query import router as query_router

app = FastAPI(title="Optical Power Toolkit API", version="2.0")

app.include_router(upload_router)
app.include_router(query_router)


@app.get("/health")
def health() -> dict[str, str]:
    """健康检查，返回固定 ok 状态。"""
    return {"status": "ok"}
