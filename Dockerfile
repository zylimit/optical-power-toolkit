# 光功率工具箱 V2.0 服务端容器镜像
# 只跑 server/（FastAPI + SQLite + 磁盘图存），不跑 OCR
# OCR 在客户端 pt_ocr.py 调 tokenplan/gemini/codex，服务端不调——镜像不装
# pillow / playwright / requests，不 COPY scripts/ 客户端代码
FROM python:3.14-slim

WORKDIR /app

# 服务端依赖（仅 fastapi/uvicorn/python-multipart；区别于根 requirements.txt
# 含客户端 pillow/playwright/requests）。先装依赖再 COPY 源码，利用层缓存
COPY server-requirements.txt /tmp/server-requirements.txt
RUN pip install --no-cache-dir -r /tmp/server-requirements.txt \
    -i https://mirrors.aliyun.com/pypi/simple

# 仅 COPY server/ + 入口；scripts/（客户端 OCR/上传模块）不进镜像
COPY server/ /app/server/

# 预建 data/ 与 images/ 目录——卷未挂时 ENV 默认 DB 落点也合法
RUN mkdir -p /app/server/data /app/server/images

# DB 默认落持久化卷内（compose / docker run 挂 ./data/server:/app/server/data）
# 代码默认是 server/toolkit.db（imagestore.IMAGES_DIR.parent），不在 data/ 卷里，
# 故此处 ENV 重定向进卷。测试可 -e OPT_DB_PATH=/tmp/test.db 覆盖指向独立库
ENV OPT_DB_PATH=/app/server/data/toolkit.db

EXPOSE 8000

CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
