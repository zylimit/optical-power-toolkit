# Development Plan — optical-power-toolkit V1.1

> 本文件记录项目的开发阶段划分、当前进度和剩余工作。
> 新 session 启动时应首先阅读此文件，了解项目状态后再继续开发。
> V1.0（xlsx 抽取 / Gemini OCR / 交叉校验入库 / 规则引擎 / 批处理）已建成，代码在 `scripts/` 下，本计划只覆盖 V1.1 三项待办。

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

## 技术栈

| 层级 | 技术 | 版本 | 说明 |
|------|------|------|------|
| 语言 | Python | 3.12 | 现有代码约束；stdlib 为主 |
| 存储 | SQLite（stdlib sqlite3） | Python 3.12 内置（SQLite ≥3.35） | 窗口函数 ROW_NUMBER 可用（需 ≥3.25，满足）；单文件零服务器 |
| HTTP | requests | requirements.txt 现有 | Gemini REST 直连 |
| 图像 | pillow | requirements.txt 现有 | OCR 前缩图 1024 |
| OCR 主模型 | gemini-3.5-flash | GA（联网验证 2026-07） | 全量 OCR，thinkingBudget:0 |
| OCR 复核模型 | gemini-3.1-pro-preview | preview（联网验证 2026-07） | 仅 Phase 3 硬样本复核 |

无新增第三方依赖，requirements.txt 不变。

## 数据库表

| 表名 | 所属 Phase | 用途 |
|------|-----------|------|
| `records` | V1.0 已有；Phase 2 增列 addr_area / addr_estate / addr_street（connect() 自动迁移旧库） | 记录主表 |
| `files` | V1.0 已有，本计划不改 | 文件级增量去重 |

盒子级报表是查询时导出的 CSV，不建物化表（规则改了随时重导，避免与 records 失同步）。

## 开发规则

- 每完成一个 Phase 执行四步走：Code Review → 测试完整性 → 编译验证 → 功能测试
- 四步走全部通过后才能 commit
- Commit message 格式：`phase-N: 简要描述`
- 包管理：pip + requirements.txt（须加 `-i https://mirrors.aliyun.com/pypi/simple` 镜像；本计划无新依赖）
- 所有新脚本开头 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`（Windows GBK 硬约束）
- 涉及 Gemini 调用的验证步骤须关 VPN；只动 SQLite 的 Phase 1 无 VPN 要求
- 图片只进本地临时目录，处理完立即删，绝不入库（信息安全硬约束）
