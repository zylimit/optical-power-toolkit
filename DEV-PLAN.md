# Development Plan — optical-power-toolkit V1.1

> 本文件记录项目的开发阶段划分、当前进度和剩余工作。
> 新 session 启动时应首先阅读此文件，了解项目状态后再继续开发。
> **V1.0/V1.1（Phase 1-3）已建成**：xlsx 抽取 / OCR / 交叉校验入库 / 规则引擎 / 批处理 / 盒子级去重报表 / 地址结构化 / 倾斜照片复核，代码在 `scripts/` 下，Phase 1-3 不再改动。
> **V2.0 服务化（Phase 4-7）·本批待开发**：客户端 CLI 上传 + 服务端存储/查询 API，详见 `Product-Spec.md` 末章「V2.0 服务化」。

---

## Phase 1: 盒子级去重复测清单（pt_report boxes）

**交付内容**：
- 新建 `scripts/pt_report.py`，提供 `boxes` 子命令：把 records 表按盒子身份归一（一盒一行），导出盒子级 CSV，供管理者直接派队复测
- 盒子身份键 `box_key` = `area + COALESCE(cluster_code,'') + COALESCE(zone,'') + hub + level + fat`；`area`/`hub`/`level`/`fat` 任一为 NULL 的记录不参与归一，单独导出到 unparsed CSV（人工处理，含原 box_name / source_file / sheet / row）
- 每盒选「最佳记录」，排序规则（SQL 窗口函数 ROW_NUMBER() OVER (PARTITION BY box_key ORDER BY ...)）：
  1. 有验证功率证据优先：`photo_status='有图清晰' AND power_check IN ('一致','表缺已恢复')`
  2. 置信度：conf 按 高 > 中 > 低 > 无效
  3. `ocr_at` 最新在前
- 盒子级复测判定（列名与记录级区分，带 `_box` 后缀）：
  - `has_evidence`='是' 当该盒**存在任一**记录满足上面第 1 条验证功率条件，否则 '否'
  - `needs_retest_box`='是' 的两种情形：① `has_evidence`='否' → `retest_reason_box`='无有效证据'、`retest_priority_box`='高'；② `has_evidence`='是' 但最佳记录 `power_status_` IN ('偏弱','偏强') → `retest_reason_box`='功率不合格'、`retest_priority_box`='高'；其余 `needs_retest_box`='否'、reason/priority 置空字符串
- boxes CSV 列（固定顺序）：`box_key, area, cluster_code, zone, hub, level, fat, records_total, files_seen, best_source_file, best_sheet, best_row, best_conf, power_dbm, power_status_, lat, lon, address, test_date, has_evidence, needs_retest_box, retest_reason_box, retest_priority_box`（records_total=该盒记录数，files_seen=去重后的 source_file 数，best_* 与 power_dbm 及之后的值均取自最佳记录）
- 运行结束打印 summary：唯一盒子数 / has_evidence='是' 盒子数 / needs_retest_box='是' 盒子数（按 retest_reason_box 分布）/ unparsed 记录数
- 命令行参数：`--db`（默认 pt_data.sqlite）、`--out`（boxes CSV 路径，必填）、`--unparsed-out`（unparsed CSV 路径，默认 out 同目录 unparsed.csv）、`--where`（追加 SQL 条件，如 `"area='DST'"`）

**Task 清单**：
- **Task 1.1：pt_report.py 骨架 + 盒子归一 SQL** — 新建 `scripts/pt_report.py`（argparse 子命令结构仿 pt_db.py main()，开头 reconfigure utf-8）；实现核心 SQL：CTE 过滤 `area/hub/level/fat` 全非 NULL 的记录 → 拼 box_key → `ROW_NUMBER() OVER (PARTITION BY box_key ORDER BY 验证功率证据 DESC, conf 排名, ocr_at DESC)` 选 rn=1 为最佳记录，再按 box_key 聚合 records_total / files_seen / has_evidence。验证：`python -m py_compile scripts/pt_report.py` + 对真实库跑 SQL 数出的 box_key 数与交付内容里的 COUNT(DISTINCT ...) 口径一致
- **Task 1.2：CSV 导出 + unparsed + summary** — 在 pt_report.py 补齐：boxes CSV 按固定列序导出（utf-8-sig，同 pt_db.export）；`area/hub/level/fat` 任一 NULL 的记录导出 unparsed CSV（含 box_name/source_file/sheet/row/main_issue）；结束打印 summary 四项统计 + 带Z/不带Z 键数量观察值；接 `--db/--out/--unparsed-out/--where` 参数。验证：`python scripts/pt_report.py boxes --db <库路径> --out boxes.csv` 跑通，行数与 has_evidence 数量级对上（≈3816 / ≈643），抽 3 盒人工核对最佳记录

**关键文件**：
- `scripts/pt_report.py` — 新建；boxes 子命令：一条带窗口函数的 SQL 选最佳记录 + GROUP BY 聚合统计，csv 模块导出（utf-8-sig，与 pt_db.export 一致），argparse 子命令结构与 pt_db.py main() 相同，开头 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`

**验收标准**：
- `python -m py_compile scripts/pt_report.py` 通过
- 对真实库跑 `python scripts/pt_report.py boxes --db <库路径> --out boxes.csv`：boxes.csv 行数（去表头）等于 `SELECT COUNT(DISTINCT area||COALESCE(cluster_code,'')||COALESCE(zone,'')||hub||level||fat) FROM records WHERE area IS NOT NULL AND hub IS NOT NULL AND level IS NOT NULL AND fat IS NOT NULL` 的结果（现库量级约 3816）；`has_evidence='是'` 的盒子数量级约 643
- 抽 3 个 records_total>1 的盒子，人工比对库里该盒全部记录，确认最佳记录选择符合上面排序规则
- V1.0 现有命令未破坏：`python scripts/pt_db.py export --db <库路径> --out /tmp/regression.csv` 仍正常

**已知风险**：部分 cluster 省 Z 段（Spec 记载的 `C2_2` 变体），同一物理盒子可能因一条记录带 Z、一条不带而拆成两个 box_key——Phase 1 不做跨键合并，summary 打印带 Z 与不带 Z 的键数量供观察，问题显著再迭代

---

## Phase 2: 地址结构化（Area/Estate/Street + Off 规则）

**交付内容**：
- 修改 `scripts/pt_ocr.py` 的 PROMPT（v2）：在现有字段基础上把地址拆解为 4 个新字段——`addr_area`（LGA/行政区，如 Alimosho）、`addr_estate`（封闭小区/estate 名，无则 null）、`addr_street`（门牌号+街名，如 13 Ogundimu St）、`near_street`（布尔：照片叠加地址里没有本街名、street 取的是最近可见街名时为 true）；原 `address` 全文字段保留不动；`ocr_one()` 返回 dict 增加这 4 个键
- 完整实现 `scripts/pt_rules.py` 的 `standardize_street(street, near_street=False)`：`near_street=True` 时给街名加 `Off ` 前缀（例 `Off 13 Ogundimu St`）；已以 `Off `（不分大小写）开头则不重复加；街名为空或含 unnamed/no street name 标记则返回空字符串
- 修改 `scripts/pt_merge.py` 的 `reconcile()`：从 OCR 结果读取 `addr_area`/`addr_estate`/`addr_street`/`near_street`，street 经 `standardize_street` 处理后，返回 dict 增加键 `addr_area`/`addr_estate`/`addr_street`（三个字符串，无值为空字符串）；`有地址` 判定逻辑不变（仍看 address 全文）
- 修改 `scripts/pt_db.py`：`BASE_COLS` 与 `SCHEMA` 的 records 表增加 `addr_area TEXT, addr_estate TEXT, addr_street TEXT` 三列；`connect()` 现有 PRAGMA 迁移循环扩展为同时补派生列和这三列（旧库打开自动 ALTER）；`build_records()` 把 reconcile 返回的三个新键写入 base dict
- 小样本验证跑通：选 1 个已入库且照片较多的 xlsx，走 `pt_batch.py <目录> --mode refresh --limit 1`（重抽照片到临时目录→OCR→入库→删临时），确认三个新列有值入库

**Task 清单**：
- **Task 2.1：OCR prompt v2 + 返回字段扩展** — 改 `scripts/pt_ocr.py` PROMPT：JSON 增加 addr_area（LGA）/ addr_estate（无则 null）/ addr_street（门牌+街名）/ near_street（布尔），原 address 保留；`ocr_one()` 成功与失败两个返回 dict 都补这 4 个键（失败置 None/False）。验证：`python -m py_compile scripts/pt_ocr.py`，用 1 张真实照片单发（临时小脚本或 --limit 1）确认返回 JSON 含 4 个新键
- **Task 2.2：standardize_street 完整实现** — 改 `scripts/pt_rules.py`：函数签名扩为 `standardize_street(street, near_street=False)`；near_street=True 时加 `Off ` 前缀、已以 Off 开头（不分大小写）不重复加、空/unnamed 返回空字符串。验证：python -c 逐条跑交付内容里的 4 个用例，输出全对
- **Task 2.3：merge 透传 + db 三列迁移** — 改 `scripts/pt_merge.py` reconcile()：读 OCR 的 4 个新键，street 过 standardize_street，返回 dict 增加 addr_area/addr_estate/addr_street 三键（空值为空字符串）；改 `scripts/pt_db.py`：BASE_COLS 与 SCHEMA 增三列，connect() 迁移循环同时补派生列与三列，build_records() 写入三键。验证：py_compile 两文件 + 旧库跑 `python scripts/pt_db.py files --db <库路径>` 不报错且 PRAGMA 可见三列
- **Task 2.4：单文件 refresh 验证** — 选 1 个已入库、照片较多的 xlsx，关 VPN 跑 `python scripts/pt_batch.py <xlsx目录> --mode refresh --limit 1`；跑后查 `SELECT COUNT(*) FROM records WHERE source_file='<该文件>' AND addr_street != ''` > 0 且非空率与照片地址可读率（约60%）同量级；确认临时目录已删。此 Task 是验证性任务，无新代码，发现问题回改 2.1–2.3

**关键文件**：
- `scripts/pt_ocr.py` — PROMPT v2 + 返回字段扩展
- `scripts/pt_rules.py` — standardize_street 完整实现（现有函数只处理了空值分支）
- `scripts/pt_merge.py` — reconcile 透传结构化地址
- `scripts/pt_db.py` — 三个新列 + 迁移 + build_records 写入

**验收标准**：
- `python -m py_compile scripts/pt_ocr.py scripts/pt_rules.py scripts/pt_merge.py scripts/pt_db.py` 全部通过
- 旧库直接 `python scripts/pt_db.py files --db <库路径>` 不报错（connect 迁移自动补列，`PRAGMA table_info(records)` 能看到 addr_area/addr_estate/addr_street）
- 单文件 refresh 重跑后：`SELECT COUNT(*) FROM records WHERE source_file='<该文件>' AND addr_street != ''` 大于 0，且非空率与 Spec 记载的照片地址可读率（约 60%）同量级
- `standardize_street` 行为逐条验证：('13 Ogundimu St', True) → 'Off 13 Ogundimu St'；('Off 13 Ogundimu St', True) → 不重复加；('', False) → ''；('unnamed road', False) → ''

**已知风险与约束**：
- 重跑 OCR 需要：原始 xlsx 仍在本地下载目录 + VPN 关闭（Gemini 外网直连）
- **全库重跑不在本 Phase 做**——放到 Phase 3 prompt（v3）定稿后一次性 `pt_batch.py <目录> --mode refresh` 跑，避免 Phase 2/3 各触发一次全量 Gemini 调用；本 Phase 只验证单文件
- 存量记录（未重跑的）三个新列为 NULL/空，属预期；`enrich` 无法现算地址列（依赖 OCR），不扩展 enrich

---

## Phase 3: 倾斜照片 OCR 增强 + 硬样本 pro 复核

**交付内容**：
- 修改 `scripts/pt_ocr.py` 的 PROMPT（v3，在 v2 基础上追加）：加入倾斜/异焦平面引导——功率计 LCD 与盒子标牌不在同一焦平面时，两者分别就近取清晰读数、互不牵连；倾斜标牌逐字符辨认，任一字符不确定即 `legible=false`，禁止按常见格式脑补
- 新建 `scripts/pt_recheck.py`：硬样本 pro 复核流水——
  1. 从库筛硬样本行：`photo_status IN ('有图模糊','OCR失败') OR box_check='标牌糊未核对'`，按 source_file 分组，支持 `--limit N` 限制文件数
  2. 对每个命中文件：调 pt_extract 重抽照片到本地临时目录 → 只对硬样本行调 `ocr_one`（模型用 `--model` 参数，默认 `gemini-3.1-pro-preview`）→ 覆写该行对应的 OCR 结果 json（沿用 pt_ocr 的 result_path 命名规则：`source_file__sheet` 清洗 + 行号）→ 用 `pt_db.store(..., mode="refresh")` 重建该文件全部记录入库 → **删除临时目录**（图片绝不入库/绝不残留）
  3. 打印 before/after 对比：复核行数、legible 由 false 转 true 数、box_check 由非'通过'转'通过'数
  - 命令行参数：`--db`、`--dir`（xlsx 下载目录）、`--model`（默认 gemini-3.1-pro-preview）、`--limit`、`--workers`
- 输出全库 prompt v3 重跑指引（写入本 Phase 完成后的操作说明，不是代码）：`python scripts/pt_batch.py <xlsx目录> --mode refresh`，一次重跑同时生效 Phase 2 的地址结构化与 Phase 3 的倾斜增强

**Task 清单**：
- **Task 3.1：OCR prompt v3 倾斜/异焦引导** — 改 `scripts/pt_ocr.py` PROMPT（在 v2 上追加）：功率计 LCD 与标牌不同焦平面时分别就近取清晰读数互不牵连；倾斜标牌逐字符辨认，任一字符不确定即 legible=false，禁止按常见格式脑补。验证：`python -m py_compile scripts/pt_ocr.py`，对 2~3 张已知倾斜/异焦样本照片单发对比 v2/v3 输出，v3 不出现脑补整串标牌的情况
- **Task 3.2：pt_recheck.py 硬样本复核流水** — 新建 `scripts/pt_recheck.py`：按交付内容三步流水实现（筛硬样本 → 按 source_file 重抽到临时目录 → ocr_one 用 --model 复核 → 覆写 OCR json（沿用 pt_ocr result_path 命名规则）→ pt_db.store refresh 入库 → 删临时目录 → 打印 before/after 三项计数）；复用 pt_extract / pt_ocr.ocr_one / pt_db.store，不复制其逻辑；参数 --db/--dir/--model（默认 gemini-3.1-pro-preview）/--limit/--workers。验证：`python -m py_compile scripts/pt_recheck.py`
- **Task 3.3：单文件复核验证 + 全库重跑指引** — 关 VPN 跑 `python scripts/pt_recheck.py --db <库路径> --dir <xlsx目录> --limit 1`：打印 before/after，库中该文件硬样本行 model 变为 gemini-3.1-pro-preview、ocr_at 刷新，临时目录已删净，legible=true 行数不劣化；在 progress.md 记下全库 v3 重跑指引（`python scripts/pt_batch.py <xlsx目录> --mode refresh`，一次生效 Phase 2+3 的 prompt 变更）。此 Task 是验证性任务，发现问题回改 3.1–3.2

**关键文件**：
- `scripts/pt_ocr.py` — PROMPT v3 倾斜/异焦引导
- `scripts/pt_recheck.py` — 新建；筛硬样本 → 重抽 → pro 复核 → 回写入库 → 清临时，复用 pt_extract / pt_ocr.ocr_one / pt_db.store，不复制其逻辑

**验收标准**：
- `python -m py_compile scripts/pt_ocr.py scripts/pt_recheck.py` 通过
- 对 1 个含硬样本的文件跑 `python scripts/pt_recheck.py --db <库路径> --dir <xlsx目录> --limit 1`：打印 before/after 计数，库中该文件硬样本行的 model 列变为 gemini-3.1-pro-preview、ocr_at 刷新
- 跑完后临时目录不存在（`pt_tmp/` 或脚本自建临时目录已删净）
- 复核不劣化：after 的 legible=true 行数 ≥ before（pro 模型只应更准；若个别行变差，打印明细供人工裁定）

**已知风险与约束**：
- 模型 id 已联网验证（2026-07）：pro 系列当前唯一可用 id 是 `gemini-3.1-pro-preview`（不存在 `gemini-3.1-pro`；Spec 简写按此落地），GA 的 `gemini-3.5-flash` 不变
- preview 模型可能改版/下线，pt_recheck 的 `--model` 做成参数正是为此
- pro 模型比 flash 慢且贵，只用于硬样本子集（全库约几百到几千行量级），不用于全量

---

## Phase 4: 服务端骨架 + 数据层（V2.0 地基）

**交付内容**：
- 新建 `server/` 目录（FastAPI 服务端，与 `scripts/` 客户端脚本平级）
- `server/app.py`：FastAPI 实例 + `GET /health` 返回 `{"status":"ok"}`；路由注册入口（后续 Phase 的路由在此挂载）
- `server/db.py`：SQLite 连接 + schema 初始化。沿用 `scripts/pt_db.py` 的 records/files 表结构与字段（不复制逻辑，可 import pt_db 或重建 schema SQL），并在其基础上扩展：
  - `files` 表新增 `content_md5 TEXT`、`file_size INTEGER`、`duplicate_of TEXT`（默认 NULL）
  - `records` 表新增 `image_path TEXT`（默认 NULL）
  - `connect()` 迁移逻辑：旧库打开自动 ALTER 补这些列（幂等，沿用 pt_db 的 PRAGMA 迁移模式）
- `server/imagestore.py`：原图磁盘存储。路径按 `content_md5/sheet/row` 组织（同内容文件图只存一份）；提供 `save_image(content_md5, sheet, row, bytes)->path`、`get_image_path(...)`、`exists(...)`、`delete_image(...)`
- `requirements.txt` 追加 `fastapi==0.140.7`、`uvicorn`、`python-multipart`（联网核验 2026-07，FastAPI 最新稳定版 0.140.7，multipart 上传需 python-multipart）

**Task 清单**：
- **Task 4.1：FastAPI 骨架 + /health** — 新建 `server/app.py`（FastAPI 实例、`sys.stdout.reconfigure` 不需要——服务端非控制台批处理，但日志中文走 utf-8 仍建议配置）、`GET /health`。新建 `server/__init__.py`。验证：`python -m py_compile server/app.py` + `uvicorn server.app:app` 起来后 `curl localhost:8000/health` 返回 200 `{"status":"ok"}`
- **Task 4.2：db.py schema + 迁移** — 新建 `server/db.py`：records/files 表 schema（沿用 pt_db 字段 + 新增 content_md5/file_size/duplicate_of/image_path）、`connect()` 自动迁移补列。验证：`py_compile` + 新建空 DB 跑 `PRAGMA table_info(files)`/`PRAGMA table_info(records)` 看到新列；拿一个 V1 旧库（`pt_data.sqlite` 副本）打开不报错且补出新列
- **Task 4.3：imagestore.py 图存磁盘** — 新建 `server/imagestore.py`：`save_image/get_image_path/exists/delete_image`，路径 `IMAGES_DIR/content_md5/sheet/row.jpg`。验证：`py_compile` + python 写一张测试图、读路径、exists、delete 四步跑通

**关键文件**：
- `server/app.py`、`server/db.py`、`server/imagestore.py`、`server/__init__.py`（均新建）
- `requirements.txt`（追加 3 个依赖）

**验收标准**：
- `python -m py_compile server/app.py server/db.py server/imagestore.py` 通过
- `uvicorn server.app:app` 启动无错，`curl localhost:8000/health` → 200 `{"status":"ok"}`
- 新建 DB schema 含全部新列；V1 旧库副本打开自动迁移不报错
- imagestore 四个函数（save/get/exists/delete）跑通

**已知风险**：服务端 DB schema 沿用 V1 pt_db 结构，但 pt_db.py 的迁移逻辑在 `scripts/` 下——Phase 4 决定是 import 复用还是重建 schema SQL（dev-builder 阶段定，倾向重建独立 schema SQL，避免服务端依赖 scripts/ 客户端代码）

---

## Phase 5: 上传接口 + 文件去重（A）

**交付内容**：
- `POST /files/{source_file}/records`：接收客户端批量提交的"记录 + 原图"（multipart，每批 ≤50 行/图）。路径参数 `source_file` 作唯一键组成。请求体含：content_md5、file_size、records（JSON 数组，每条含 V1 records 全字段 + sheet + row）、images（图二进制，按 sheet+row 索引）
- 服务端文件去重（A）：按 `content_md5 + file_size` 双校验：
  - 不存在 → 新入库（存图 + 存记录，files 表插一条含 md5/size）
  - 同 source_file 同 md5+size → 记录 upsert 覆盖（沿用 V1 唯一键 (source_file,sheet,row)）
  - 不同 source_file 同 md5+size → 标 `duplicate_of=原文件 source_file`、**只存记录不重存图**（图复用，imagestore 已按 content_md5 组织）、不自动删
- 图落磁盘经 `imagestore.save_image`（content_md5/sheet/row），DB records 存 `image_path`；覆盖时先写新图→更新 DB 路径→删旧图，事务保证（sqlite3 事务 + imagestore 操作顺序）
- `GET /files?since=<ts>`：返回 `[{source_file, content_md5, file_size, record_count, duplicate_of, ocr_at}]`，供客户端上传前查增量跳过

**Task 清单**：
- **Task 5.1：上传接口 + multipart 解析 + 记录 upsert** — `server/routes_upload.py`（或直接 app.py）：`POST /files/{source_file}/records`，python-multipart 解析 records JSON + images，记录按 (source_file,sheet,row) upsert 入库。**content_md5 格式校验（Phase 4 red-locks 前置约束）**：接口入口校验 content_md5 匹配 md5 摘要格式，非法直接返回 400（imagestore 库层已有 `[/\\:]|\.\.` traversal 黑名单兜底，接口层再挡一道；此处也是 TODO #12 白名单权衡的落点——确认 content_md5 恒为 md5 摘要则接口用 `^[0-9a-fA-F]{32}$` 白名单）。验证：`py_compile` + curl 上传一个文件的一批记录（含 2-3 行+图），DB 有记录、磁盘有图、image_path 非空；**curl 传恶意 content_md5（`../../etc`）应返回 400**（red-locks 用例，tester 补失败测试锁定）
- **Task 5.2：文件去重双校验 + duplicate_of 标记 + 图不重存** — 上传接口内加文件去重逻辑：算/取 content_md5+file_size，三分支（新入库/upsert 覆盖/标 duplicate_of 不重存图）。验证：curl 传同 source_file 同内容两次→覆盖幂等记录数不变；传不同 source_file 同内容→第二次 duplicate_of 标记、磁盘图不增
- **Task 5.3：GET /files 增量接口** — `GET /files?since=<ts>` 返回文件清单。验证：curl 查返回 JSON 数组含已上传文件，since 过滤生效

**关键文件**：
- `server/app.py`（挂载上传路由）、`server/routes_upload.py`（新建，上传 + GET /files）、`server/db.py`（加 upsert/查询函数）、`server/imagestore.py`（已建，调用）

**验收标准**：
- `py_compile` 通过
- curl 上传记录+图 → DB records 有、磁盘图存在、image_path 写入
- 同文件同内容重传 → 记录数不变（覆盖幂等）
- 不同文件同内容 → 第二次 duplicate_of 非空、磁盘图数不增（图复用）
- 覆盖时旧图被删（imagestore 无悬空旧图）
- `curl "localhost:8000/files"` 返回已上传文件清单

**已知风险**：multipart 每批 ≤50 图，单文件上千图需客户端分多批 POST（Phase 7 客户端实现分批）；服务端单批大小限制由 uvicorn/python-multipart 默认配置，必要时调参

---

## Phase 6: 查询 API + 记录合并（B）

**交付内容**：
- `GET /records`：批量拉记录列表。查询参数：area/zone/hub/box_name/needs_retest/photo_status/conf（任选筛）、page/page_size（分页，默认 page=1 page_size=50）、sort（排序，默认 ocr_at desc）。**记录合并在查询时用窗口函数算，不物化**（沿用 V1 pt_report.py 思路）：
  - 合并键 fallback：L1 `area+cluster_code+zone+hub+level+fat` 严格相等 → L3 `box_name` 原值清洗后相等 → 都不中标 `box_key_missing`（孤儿）
  - 选赢家 ORDER BY：`evidence DESC, conf_rank ASC, ocr_at DESC`；`evidence=1` 当 `photo_status='有图清晰'`（V2.0 放宽，去掉 V1 的 `AND power_check IN(...)`）；模糊图/无图 evidence=0
  - 默认排除 `duplicate_of IS NOT NULL` 和 `box_key_missing`；`?include_duplicates=1` / `?include_orphans=1` 显式看全
- `GET /records/{id}`：单条记录详情
- `GET /records/{id}/image`：取该记录原图（返回 image/jpeg，FileResponse）

**Task 清单**：
- **Task 6.1：GET /records 列表 + 分页筛选 + 记录合并窗口函数** — `server/routes_query.py`：实现合并 SQL（L1/L3 fallback + evidence 放宽 + 窗口函数选赢家，可参考 `scripts/pt_report.py` 的 BOXES_SQL 但 records 级非 box 级）+ 分页筛选 + 默认排除 duplicate/orphan。验证：curl 查列表返回分页 JSON；筛 area=XXX 生效；同盒多记录只返回赢家
- **Task 6.2：单条 + 取图接口** — `GET /records/{id}` 返回单条详情；`GET /records/{id}/image` 用 imagestore 取图返回 FileResponse。验证：curl 取图返回 200 image/jpeg 内容；不存在的 id 返回 404

**关键文件**：
- `server/app.py`（挂载查询路由）、`server/routes_query.py`（新建）、`server/db.py`（加查询函数）、`server/imagestore.py`（取图）

**验收标准**：
- `py_compile` 通过
- curl `GET /records?page=1&page_size=10` 返回 ≤10 条 + 总数
- 筛选参数生效（如 `?needs_retest=是`）
- 同盒多记录（造数据：同 box_key 2 条，1 有图清晰 1 无图）→ 列表只返回有图清晰的赢家
- `GET /records/{id}/image` 返回原图二进制
- 默认排除 duplicate 和 orphan；`?include_duplicates=1` 能查到 duplicate 记录

**已知风险**：L1/L3 fallback 合并的 SQL 比较复杂（窗口函数 + 两级键匹配 + evidence），需仔细写和测；evidence 放宽后 V1 pt_report.py 离线报表的 evidence 定义与服务端不一致（V1 仍严格），本 Phase 服务端用放宽版，V1 离线报表不动

---

## Phase 7: 客户端 CLI 改造 + 端到端联调

**交付内容**：
- 改 `scripts/pt_batch.py`：第 5 步"pt_db 落库"换成"上传服务端"——校验合并后的结构化记录 + 原图，分批（每批 ≤50 行/图）`POST` 到 `{SERVER_URL}/files/{source_file}/records`，覆盖幂等
- 上传前先算 `content_md5 + file_size`（下载完的 xlsx），查 `GET {SERVER_URL}/files` 增量清单（本地缓存 + since 增量拉），命中（同 md5+size 已入库）则**跳过抽取/OCR/上传全流程**
- 客户端本地 SQLite 落库降级为可选缓存（pt_db 落库默认不走，可 `--local-db` 保留兼容）；权威数据在服务端
- `--server-url` 参数（默认 `http://localhost:8000`，V1 本机测试）
- 端到端：下载→抽取→OCR→校验→上传→服务端存→curl 查询拉回验证

**Task 清单**：
- **Task 7.1：pt_batch.py 第 5 步落库换上传 + 分批** — 改 `scripts/pt_batch.py`：第 5 步从 `pt_db.store(...)` 改为构造 multipart（records JSON + images）分批 POST 到服务端；加 `--server-url` 参数（默认 localhost:8000）；pt_db 落库降级 `--local-db`。验证：`py_compile` + 跑 `pt_batch.py <目录> --limit 1 --server-url localhost:8000`（服务端已起），服务端 DB 有该文件记录+图
- **Task 7.2：上传前 content_md5+size 查增量跳过** — pt_batch.py 下载完 xlsx 算 md5+size，查 `GET /files` 缓存清单，命中跳过全流程。验证：跑两次同文件，第二次日志显示"已入库跳过"、不调 OCR
- **Task 7.3：端到端联调** — 真实文件全流程：开 VPN 下载→抽取→关 VPN OCR→校验→开 VPN 上传→服务端存→curl 查记录+取图。验证：服务端有数据有图，`curl GET /records` 拉回该文件记录，`GET /records/{id}/image` 取回原图

**关键文件**：
- `scripts/pt_batch.py`（改第 5 步 + 加 --server-url + 增量查询）

**验收标准**：
- `py_compile scripts/pt_batch.py` 通过
- `pt_batch.py <目录> --limit 1 --server-url localhost:8000` 跑通，服务端 DB + 磁盘有该文件记录+图
- 同文件第二次跑 → 跳过全流程（不调 OCR，日志确认）
- 端到端：curl 查回记录、取回原图
- V1 原有 `--local-db` 兼容模式仍能落本地 SQLite（不破坏）

**已知风险**：VPN 切换在客户端（开下/关 OCR/开上传）——V2.0 不解决 VPN 互斥，需用户手动切或脚本提示；端到端联调需真实数据+VPN 窗口，可能受网络/OCR 额度影响

---

## 多 Agent 编排评估（用户已提"多 agent 开发"）

按框架铁律：**编码默认串行**（共享 DB schema/路由注册，非真正独立）。V2.0 Phase 4-7 依赖链 4→5→6→7，共享 `server/db.py` schema 与 `server/app.py` 路由注册，并行会冲突。
- **可并行候选**：Phase 5（上传写）与 Phase 6（查询读）在 Phase 4 钉死 schema + 接口契约后，可 worktree 隔离并行（不同路由文件 routes_upload.py / routes_query.py，不共改同文件），主 Agent 合并。
- **真正独立甜区**（适合 Workflow fan-out）：Phase 6 完成后的 code-review 多维度并行审查、tester 批量写回归测试——只读/可汇总工作。
- 建议：**Phase 4 串行地基 → Phase 5/6 评估 worktree 并行（须用户 opt-in Workflow，成本 ~15x）→ Phase 7 串行（依赖上传接口）**。是否用 Workflow 并行编码由用户拍板。

---

## 技术栈

| 层级 | 技术 | 版本 | 说明 |
|------|------|------|------|
| 语言 | Python | 3.12 | 现有代码约束；stdlib 为主 |
| 存储 | SQLite（stdlib sqlite3） | Python 3.12 内置（SQLite ≥3.35） | 窗口函数 ROW_NUMBER 可用（需 ≥3.25，满足）；单文件零服务器 |
| HTTP 客户端 | requests | requirements.txt 现有 | Gemini REST 直连；V2.0 客户端上传也用 |
| 图像 | pillow | requirements.txt 现有 | OCR 前缩图 1024 |
| OCR 主模型 | gemini-3.5-flash | GA（联网验证 2026-07） | 全量 OCR，thinkingBudget:0 |
| OCR 复核模型 | gemini-3.1-pro-preview | preview（联网验证 2026-07） | 仅 Phase 3 硬样本复核 |
| **V2.0 服务端框架** | **FastAPI** | **0.140.7（联网核验 2026-07 PyPI）** | Python，与现有脚本同语言；自带 OpenAPI；异步上传图友好 |
| **V2.0 服务端 ASGI** | **uvicorn** | **最新稳定版** | FastAPI 运行器 |
| **V2.0 上传解析** | **python-multipart** | **最新稳定版** | FastAPI multipart 文件上传必需 |

V2.0 新增 3 个服务端依赖（fastapi==0.140.7、uvicorn、python-multipart），写入 requirements.txt；pip 安装加镜像 `-i https://mirrors.aliyun.com/pypi/simple`。

## 数据库表

| 表名 | 所属 Phase | 用途 |
|------|-----------|------|
| `records` | V1.0 已有；Phase 2 增 addr_area/addr_estate/addr_street；**Phase 4 增 image_path** | 记录主表 |
| `files` | V1.0 已有；**Phase 4 增 content_md5/file_size/duplicate_of** | 文件级增量去重 + 跨文件内容去重标记 |

V2.0 图存磁盘（`server/imagestore.py` 按 content_md5/sheet/row 组织），不入 DB blob。
记录合并在查询时用窗口函数算，不建物化盒子表（沿用 V1 pt_report.py 思路，规则改随时重算）。

## 开发规则

- 每完成一个 Phase 执行四步走：Code Review → 测试完整性 → 编译验证 → 功能测试
- 四步走全部通过后才能 commit
- Commit message 格式：`phase-N: 简要描述`
- 包管理：pip + requirements.txt（须加 `-i https://mirrors.aliyun.com/pypi/simple` 镜像；本计划无新依赖）
- 所有新脚本开头 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`（Windows GBK 硬约束）
- 涉及 Gemini 调用的验证步骤须关 VPN；只动 SQLite 的 Phase 1 无 VPN 要求
- ~~图片只进本地临时目录，处理完立即删，绝不入库~~ → **V2.0 修订**：内网闭环、不外传前提下，服务端可存原图（磁盘 + DB 存路径）；客户端临时图片用完即删保留；服务端原图持久化（OCR 溯源：每条记录 image_path 指向产生其 OCR 结果的原图）
- **V2.0 服务端**：`server/` 目录，`uvicorn server.app:app` 启动；服务端 DB 沿用 pt_db schema + Phase 4 新增字段；V2.0 Phase 4-7 commit message 格式 `phase-N-v2: 简要描述`
- **V2.0 编排**：Phase 4-7 默认串行（共享 schema/路由）；Phase 5/6 worktree 并行须经用户 opt-in Workflow
