# 光功率工具箱 V2.0 服务端部署

容器化部署到内网 PaaS（7.183.255.169）。镜像只跑服务端（FastAPI + SQLite +
磁盘图存），不含 OCR——OCR 在客户端 `pt_ocr.py` 调 tokenplan/gemini/codex。

## 文件清单

| 文件 | 作用 |
|------|------|
| `Dockerfile` | 镜像构建（python:3.14-slim + 仅服务端依赖） |
| `server-requirements.txt` | 服务端依赖（fastapi/uvicorn/python-multipart） |
| `docker-compose.yml` | 编排 + 卷持久化（DB + images） |
| `.dockerignore` | 排除 scripts/ tests/ .venv/ 文档/数据/密钥 |

## 镜像内容边界

- 只 COPY `server/` 到 `/app/server/`，**不 COPY `scripts/`**（客户端 OCR/上传）
- 只装 `fastapi==0.140.7` / `uvicorn` / `python-multipart`，**不装**
  `pillow` / `playwright` / `requests`（客户端 OCR/下载用）
- 不硬编码任何 key/密码；`.tokenplan_key` 被 .dockerignore 排除

## 路径映射（关键）

容器内固定路径（来自代码确认）：

| 项 | 容器内路径 | 来源 |
|----|-----------|------|
| DB | `/app/server/data/toolkit.db` | `OPT_DB_PATH` ENV（Dockerfile） |
| 原图 | `/app/server/images` | `imagestore.IMAGES_DIR = Path(__file__).parent/images` |

> 代码默认 DB 路径是 `server/toolkit.db`（`imagestore.IMAGES_DIR.parent`），
> 不在持久化卷内。Dockerfile 用 `ENV OPT_DB_PATH=/app/server/data/toolkit.db`
> 重定向进 `data/` 卷。测试可 `-e OPT_DB_PATH=/tmp/test.db` 覆盖指向独立库。

## 部署流程

### 0. 前置（一次性）

确认本机有 docker 且能 SSH/scp 到腾讯云 build 机和内网 PaaS。
SSH 凭据 / 密钥走环境变量或 secret 注入，**不在命令行写明文密码**：

```bash
# 示例：用环境变量带 SSH key 路径（不硬编码）
export SSH_KEY=~/.ssh/id_ed25519
export BUILD_HOST=user@build.tencent.cloud
export PAAS_HOST=root@7.183.255.169
```

### 1. 腾讯云 build 镜像

把项目代码传到腾讯云 build 机（git clone 或 rsync），在项目根执行：

```bash
docker build -t optical-power-toolkit:latest .
```

构建后确认镜像只含 server/（无 scripts/）：

```bash
docker run --rm optical-power-toolkit:latest ls -la /app/server
# 应只见 server/ 下 .py 文件，无 scripts/
```

### 2. save 镜像为 tar

```bash
# 不压缩
docker save optical-power-toolkit:latest -o opt.tar

# 或 gzip 压缩（传输体积小，load 时自动解）
docker save optical-power-toolkit:latest | gzip > opt.tar.gz
```

### 3. 下载到本地

从腾讯云 build 机 scp 下来（走 build 机的 SSH 通道，凭据见步骤 0）：

```bash
scp -i "$SSH_KEY" "$BUILD_HOST":~/opt.tar .
# 或压缩版
scp -i "$SSH_KEY" "$BUILD_HOST":~/opt.tar.gz .
```

### 4. 启 VPN

启连内网 PaaS 的 VPN（用户手动操作，确保能 ping 通 7.183.255.169）。

### 5. 传内网 PaaS

```bash
scp -i "$SSH_KEY" opt.tar "$PAAS_HOST":~/
# 或压缩版
scp -i "$SSH_KEY" opt.tar.gz "$PAAS_HOST":~/
```

### 6. PaaS load 镜像

```bash
ssh -i "$SSH_KEY" "$PAAS_HOST" "docker load -i opt.tar"
# 压缩版：docker load < opt.tar.gz
```

确认镜像到位：

```bash
ssh -i "$SSH_KEY" "$PAAS_HOST" "docker images optical-power-toolkit"
```

### 7. PaaS run 容器

> 目标机实测 Docker **18.09.0**（EulerOS PaaS）。该环境 `-p 8000:8000` 端口映射可能不生效
> （`NetworkSettings.Ports` 为空、宿主机 8000 不监听）。**生产已验证用 `--network host`**。

**方式 A——docker run（推荐，host 网络 + /srv 数据盘）**：

```bash
# 数据落 /srv（66G 可用），不要放根盘 /
ssh root@7.183.255.169 'mkdir -p /srv/optical-power-toolkit/data/server /srv/optical-power-toolkit/data/images && \
  docker rm -f optical-power-toolkit-server 2>/dev/null || true && \
  docker run -d \
    --name optical-power-toolkit-server \
    --network host \
    -v /srv/optical-power-toolkit/data/server:/app/server/data \
    -v /srv/optical-power-toolkit/data/images:/app/server/images \
    --restart unless-stopped \
    optical-power-toolkit:latest'
```

**方式 B——docker-compose**：

把 `docker-compose.yml` 传到 PaaS，改卷路径为 `/srv/...`，并加 `network_mode: host`
（18.09 下比 ports 映射稳）。然后：

```bash
mkdir -p /srv/optical-power-toolkit/data/server /srv/optical-power-toolkit/data/images
docker compose up -d
```

### 8. 验证

```bash
# 健康检查
curl http://7.183.255.169:8000/health
# 期望：{"status":"ok"}

# 容器状态
ssh -i "$SSH_KEY" "$PAAS_HOST" "docker ps | grep optical-power-toolkit"

# DB / 图存卷已挂载（路径非空即说明挂上）
ssh -i "$SSH_KEY" "$PAAS_HOST" "docker exec optical-power-toolkit-server ls -la /app/server/data /app/server/images"
```

## 常见问题

- **DB 不持久化（容器重启数据丢）**：确认卷映射路径与 `OPT_DB_PATH` ENV 一致
  （`/app/server/data` ↔ `OPT_DB_PATH=/app/server/data/toolkit.db`）。docker run
  方式漏挂 `-v ./data/server:/app/server/data` 会回退到代码默认
  `/app/server/toolkit.db`（容器内 ephemeral）。
- **图存不持久化**：确认挂了 `-v ./data/images:/app/server/images`，
  否则容器重建丢全部原图。
- **端口不通**：PaaS 防火墙放行 8000；VPN 已连。
- **密钥泄露**：`.tokenplan_key` 已被 .dockerignore 排除；如需服务端调 OCR
  （当前设计不调），用 `-e` 或 secret 注入，绝不写进镜像层。
