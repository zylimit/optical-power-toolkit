# 变更记录

## [v2.0] - 2026-07-28
### 新增
- 新增「V2.0 服务化」章节：客户端 CLI 上传 + 服务端存储/查询 API 架构（方案A 客户端全栈）——客户端复用 pt_batch.py 全流程（下载→抽取→OCR→校验），把"落本地 SQLite"换成"上传服务端"；服务端从零写，存图(磁盘)+存数据(DB)+REST API 供 digifiber-conflation 批量拉光功率记录与原图
- 新增服务端查询 API：批量拉记录列表（按 area/zone/hub/box_name/needs_retest 筛、分页）+ 按需取原图接口，供 digifiber-conflation 下游程序化消费
- 新增服务端上传接口：按 (source_file, sheet, row) 唯一键覆盖幂等，原图落磁盘、DB 存路径
- 新增去重/合并两套机制：A.文件去重（入库时物化 duplicate_of，权威键 content_md5+file_size 双校验，同内容文件图只存一份按 content_md5/sheet/row 组织）；B.记录合并（查询时窗口函数算不物化，合并键 fallback L1 严格→L3 box_name 原值→孤儿留人工）
- 新增合并选赢家规则：evidence DESC > conf_rank ASC > ocr_at DESC；evidence=有图清晰（V2.0 放宽，去掉 V1 的 power_check 条件，功率以图为准）
- 新增增量去重接口 GET /files?since=（含 content_md5/file_size/duplicate_of），客户端上传前查增量跳过全流程
- 新增接口清单（#1-#6 必要：health/files/上传/records 列表/单条/取图；#7-#9 盒子聚合/CSV/统计后置）

### 修改
- 修订 V1 硬约束「图片绝不入库」：内网闭环、不外传前提下，服务端可存原图（约束从"绝不"改为"内网可存"）；客户端临时图片用完即删保留
- 状态分期新增 V2.0（服务化·本迭代）条目，置于 V1.1 与北极星 V2 之间
- 数据存储技术方向：服务端 V1 本机测试沿用 SQLite + pt_db schema（零迁移），图存磁盘不入 blob；生产内网部署时再评估迁 PostgreSQL

### 决策（记入 progress.md Decisions）
- 架构方案定方案A（客户端全栈），放弃服务端自动下载：权衡 4 方案后认定"服务端下载→客户端 OCR"路线的跨端传图 + VPN 反复切代价大于"下载留客户端"；图片从来在客户端、只客户端→服务端传一次，复用最大（5 步里 4 步原样），工程量最小
- OCR 留客户端：服务端内网不能联外网调 Gemini/Codex CLI，OCR 必须在客户端（延续 V1 pt_ocr 双后端，走订阅额度不按量计费）
- 文件去重权威键 = content_md5 + file_size 双校验，不靠文件名（V1 踩过同名不同内容 Dawaki 双版本；加 size 兜 zip 重打包漏网）
- 记录合并键 fallback 砍 L2 area 容错档，走 L1 严格→L3 原值→孤儿：合并键来自表格 box_name（area 干净字母无 OCR 混淆可兜），area 模糊匹配无可靠判据、误并风险高于收益，V1 合规率 98% 支撑 L1 已覆盖大多数
- evidence 放宽：去掉 V1 `AND power_check IN('一致','表缺已恢复')`，只要有图清晰即证据（功率以图为准，表格填错不影响图可信度）
- 记录合并查询时算不物化（沿用 V1 pt_report.py 思路，规则改随时重算）

### 待 V1 本机测试验证
- content_md5+file_size 双校验能否兜住 xlsx 同内容同大小不同字节的极端漏网
- L3 box_name 原值相等的误并概率（两不同盒原值恰好同写法，概率极低但非零）

### V2.0 明确不做
- 服务端自动下载、同时通内外网/VPN 自动切换、Web 前端/看板、服务端 OCR（本地视觉模型）、PostgreSQL 迁移、多用户/权限、L2 area 容错合并（留人工兜底）

---

## [v1.0] - 2026-07-10
- 初始版本：光功率测试数据处理系统（xlsx 抽取 + Gemini 视觉 OCR + 表图交叉校验 + FAT 命名规则引擎 + 功率合格判定 + SQLite 入库 + 复测清单/报表）
