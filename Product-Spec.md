# Product Spec — 光功率测试数据处理系统（optical-power-toolkit）

> 版本：V1（Excel 照片 → 结构化数据库 → 复测清单/报表）
> 日期：2026-07-10 · 从 web-control 的 PT 处理专题独立成工程
> 迭代原则：小步。本 Spec 覆盖已建成的处理管线 + 数据模型 + 规则引擎，报表/去重/地址结构化在后续迭代扩展。
>
> **状态分期**：
> - **V1.0（已建成）**：xlsx 抽取（表+锚定照片）、Gemini 视觉 OCR、表图交叉校验、FAT 命名规则引擎、功率合格判定、SQLite 入库（增量/覆盖/询问三模式）、派生指标（层级/复测清单）、CSV 导出。
> - **V1.1（待做）**：盒子级去重报表（管理者复测清单）、地址结构化（Area/Estate/Street + Off 规则）、倾斜/异焦平面照片 OCR 增强。
> - **V2.0（服务化·本迭代）**：客户端 CLI 上传 + 服务端存储/查询 API——把 V1 本地处理管线改造成 C/S：客户端复用 pt_batch.py 全流程（下载→抽取→OCR→校验），把"落本地 SQLite"换成"上传服务端"；服务端从零写，存图(磁盘)+存数据(DB)+提供 REST API 供 digifiber-conflation 批量拉光功率记录与原图。详见末章「V2.0 服务化」。
> - **V2（北极星）**：与 digifiber-conflation 打通——用光功率坐标修 NCE 的 GPS 漂移、按 FAT 名和设计库对覆盖率。

## 产品概述

面向 FTTH 交付团队的**光功率测试数据处理与质检工具**（命令行/批处理，无 UI）。

**解决的问题**：现场用光功率计逐个测 FAT 盒子，用 GPS Map Camera 拍照存证，整理成 Excel（xlsx）上传到 WeLink 云空间。每个 xlsx 一行一个盒子 + 一张照片，照片里烧录了盒子标牌名、功率计读数、经纬度、地址、时间。但这批数据**极脏**：多 sheet 混杂（有的根本不是光功率表）、命名/列布局不统一、漏填、填错、甚至**无图却判 Pass**（造假）。人工核对上万条不现实。目标：把照片+表格**自动解析成结构化、可信、可去重、可导出的数据库**，用于质检、驱动管理者派队复测、导出各类报表。

**目标用户**：交付/质量工程师与他们的管理者（华为内网环境）。

**核心价值**：
1. **照片即证据的自动核验**：Gemini 视觉读照片里的功率/盒子名/坐标/地址，和人填的表格**交叉校验**——两个独立来源九成以上一致，对不上的就是漏填/填错/造假。
2. **规则化数据清洗**：按 FAT 命名语法校正 OCR 噪声、按功率阈值判合格，产出干净结构化数据。
3. **驱动复测**：算出"哪些盒子要重新上站测"的行动清单（原因+优先级+定位），给管理者派队。
4. **留全数据、随时出报表**：一次入库，多种报表随时导出。

**V1 范围内**：从 WeLink 云空间**下载** xlsx（onebox_downloader）→ 解析 → OCR → 入库 → 导出，全链自包含。

**V1 明确不做**：
- KMZ 设计侧处理（kmz_toolkit / digifiber-conflation 负责）
- Web UI（V1 纯命令行/批处理）
- 图片入库（~~信息安全硬约束：图片绝不进库~~ → **V2.0 修订**：内网闭环、不外传前提下，服务端可存原图，详见末章「V2.0 服务化·约束修订」）

## 产品定位与路线图

### 内核定位：把"照片+表格"变成"可信结构化数据"
本工具是 digifiber-conflation 修正体系里的**光功率修正源生产端**。digifiber-conflation 拿光功率数据去修 NCE 网管的 GPS 漂移、核对 FAT 覆盖。本工具只负责把散乱的 PT Excel **处理成干净可查的库**，不做地图/放号。

### 领域模型（判断数据对错的标尺）

**FAT 命名语法**（例 `DSTC1Z3H2L1S4`，判断盒子名合规/解析层级）：
| 段 | 含义 | 固定规则 | 常见 OCR 误读 |
|---|---|---|---|
| **DST** | cluster 名简写（AREA） | 只含字母，无数字 | D/O→0、B→8、I/J→1 |
| **C1** | Cluster 区域 | C + 1 位数字 | — |
| **Z3** | 片区 zone | Z + 1 位数字 | Z→2 |
| **H2** | Hubbox | H + 数字（可能 HB2/H02 不规范，清洗成 H+int） | — |
| **L1** | Hubbox 端口 | L + 1 位数字，范围 1~8 | — |
| **S4** | FAT（别名 subbox/endbox） | S + 1 位数字，范围 1~4 | — |
- 变体：部分 cluster 省 Z（写成 `C2_2`）；部分只填后半段 `H##L#S#`，前缀在 cluster 列/sheet 名/文件名里（需兜底补 area）。
- 命名合规率实测 98%；area 覆盖率 98.4%。

**光功率合格判定**：读数为 dBm 负数（库存幅值正数）。**-25 < value ≤ -10 为合格**；`value ≤ -25` 偏弱（错误）、`value > -10` 偏强（错误）。偏弱/偏强 = 功率不合格 = 需复测。

**真相优先级**（哪个来源为准）：
- **功率 / 经纬度 / 地址 → 以照片为准**（功率计屏/GPS 叠加最准；实测功率读得 100%、坐标 85%、地址 60%）；
- **盒子名 → 以表格为准**（人填的干净），照片只用 `H##L#S#` 后缀 + 规则容错做核对（整串标牌 OCR 噪声大）。

## 应用场景

- **批量质检**：工程师把新下的一批 PT xlsx 丢给工具，`pt_batch.py 目录 --limit 10` 逐个吃（增量：已入库的自动跳过）。几分钟后库里多了几千条结构化记录，每条带置信度和"主要问题"。
- **管理者派复测**：管理者导出"复测清单"CSV（`needs_retest=是 且 priority=高`），按区域/Zone/Hub 分组，看到哪些盒子无证据/功率不合格，直接派队上站重测。
- **抓造假**：筛 `main_issue=无图判Pass`——那些填了 Pass 却没贴照片的行，一目了然。
- **每日增量**：云空间每天新增文件，重跑 `pt_batch.py`，只处理没入库过的，刷新库和报表。

## 功能需求

### 下载（onebox_downloader）

从 WeLink 云空间群空间下载 xlsx（Playwright 拿 SSO 会话 + `getForcedDownloadUrl` 直链，断点续传、失败重试）。
- **要开内网 VPN**（onebox 是内网）；xgate 只对可见浏览器注入登录态，会话约 1 小时过期，跑前先 `--login` 刷新。
- 大文件多（单个到 340MB），并发 2~3、断了重跑自动跳过已下好的。

### 处理流水线（三段 + 编排）

```
下载 ──▶ xlsx ──①抽取──▶ 行(表格数据)+锚定照片 ──②Gemini OCR──▶ 照片结构化 ──③校验入库──▶ SQLite
(onebox)       pt_extract      (临时目录)            pt_ocr          pt_merge/pt_db    (唯一持久层)
                                                                                     用完删临时图片
```

- **① 抽取（pt_extract.py）**：xlsx 是 zip，解 XML。
  - **认光功率 sheet**（文件很乱、多 sheet 非光功率）：判据 = 盒子ID(`H##L#S#`)出现 ≥3 次 且（有 power 关键词 或 有锚定照片）。排掉 OLT 设计页、空壳页。
  - **列自适应**（不同 sheet 列布局不同，不能写死 A/B/D/F）：靠数据特征定位——盒子列=`H##L#S#`最多的列、功率列=数值落在[0.5,60]最多的列、Pass列=Pass/Fail最多的列，cluster/date 按表头关键词。
  - **照片按行锚定**：解 drawing 的 `xdr:from` 行号 + `a:blip` → media，把每张照片映射到表格行。
- **② Gemini OCR（pt_ocr.py）**：每张照片 → JSON（box_name / power_dbm / lat / lon / address / timestamp / legible）。
  - **本机直连 Gemini**（REST，`GEMINI_API_KEY`），绕开内网推理网关的超时；**VPN 必须关**（Gemini 走外网）。默认模型 `gemini-3.5-flash`（3 代精度 + flash 速度；比 2.5-flash 标牌读得更准）。
  - 关思考（thinkingBudget:0）省时；缩图 1024（快 1.8x、成本一样、准确率不差）；并发线程池；**断点续传**（一图一 json，已做的跳过）；网络抖动 5 次重试 + 退避。
- **③ 校验入库（pt_merge.py + pt_db.py）**：
  - **交叉校验**：表 vs 图，功率核对（一致/表缺已恢复/表图不符/图糊用表）、盒子核对（后缀 + 规则容错：match/mismatch/unreadable）。
  - **全枚举列，方便筛选**（用户强调：归类好、别塞自由文字）：置信度[高/中/低/无效]、照片状态、功率核对、盒子核对、有坐标/有地址/可修坐标、主要问题[正常/无图判Pass/功率不符/功率漏填/盒子不符/无坐标/标牌不清/无图]。
  - **规则派生列**（pt_rules.py，可从库现算无需重跑 OCR）：area/cluster/zone/hub/level/fat、fat_valid、power_status[合格/偏弱/偏强/无值]、needs_retest、retest_reason、retest_priority。

### 批处理编排（pt_batch.py）

逐文件流水：查库跳过已入库 → 抽取到**本地临时目录** → OCR → 入库 → **删临时** → 下一个。
- **增量**：`--mode skip`（默认）扫全目录，只吃没入库过的；`--limit 10` 一批 10 个。
- **覆盖**：`--mode refresh` 重跑覆盖；`--mode ask` 逐个询问。
- **0 行文件**（没检测到光功率 sheet）也登记跳过，不每批重试。

### 数据库（pt_db.py，SQLite）

- **files 表**：每个扫过的 xlsx 一条（记录数/图数/模型/时间），用于增量去重。
- **records 表**：每条记录一行，`UNIQUE(source_file, sheet, row)` → **重复就覆盖**。索引：box_name / source_file / conf / main_issue / (lon,lat) / area / (needs_retest,priority) / power_status。
- **导出**：`export` 随时导 CSV，可带 SQL 条件筛（如 `conf='高'`、`needs_retest='是' and retest_priority='高'`）。
- **enrich**：用规则引擎重算全库派生列（改了规则/阈值后一键刷新，不重跑 OCR）。

## AI 能力需求

| 能力 | 用途 | 位置 | 模型 |
|---|---|---|---|
| 视觉 OCR | 读现场照片里的盒子标牌名、功率计读数、GPS 经纬度、地址、时间 | pt_ocr.py | gemini-3.5-flash（直连，可换 3.1-pro 复核硬样本） |

其余环节（sheet 识别、列定位、命名校正、功率判定、复测决策）**全是确定性规则**，无 AI——规则可解释、可审计、零成本。

## 技术方向

| 维度 | 选择 | 理由 |
|---|---|---|
| 形态 | 命令行/批处理脚本（Python） | 后台批量处理，无需 UI |
| 语言 | Python 3.x（stdlib 为主） | 解 xlsx/xml 用 zipfile+ElementTree，无需 openpyxl |
| OCR | Gemini REST 直连（requests + Pillow 缩图） | 绕内网网关超时；Ultra 订阅量足 |
| 存储 | **SQLite**（单文件 + 索引） | 十万级记录足够；单文件可拷、零服务器；图片绝不入库 |
| 依赖 | requests、pillow | 极简；不用 SDK/openpyxl/shapely |

## 技术说明（硬约束 + 踩过的坑）

- **图片绝不入库（信息安全硬约束）**：DB 只存文本/数值数据；图片仅在本地临时目录过一次 OCR，**每个文件处理完立即删**（`pt_tmp/` 用完即清）。持久层零图片。
- **VPN 状态相反**：下载 xlsx 要**开**内网 VPN（onebox 内网）；OCR 调 Gemini 要**关** VPN（外网）。两阶段不能同时。
- **键必须带源文件**：多个文件常有**相同 sheet 名**（如两版 IJU 都有"iju C1 Z1"），OCR 结果文件名和合并键必须带 `source_file`，否则跨文件同行号覆盖——踩过一次（盒子误报从 189 降到 16）。同理跨 sheet 撞行号也修过（按 (source_file, sheet, row) 索引）。
- **OCR 结果文件名清洗正则曾写错** `r'\\w'` → 所有 sheet 名清洗成同一个 `_`、文件名撞车、只写了一半还误判"全跑完"——教训：命名清洗正则要测。
- **功率列会混进日期序列号**（某些 sheet 的功率列实为 Excel 日期 45883），合并时对功率做合理性 sanity（超出 dBm 幅度 >60 当无效，退用照片值）。
- **空 drawing 无 .rels** 会让照片锚定崩溃 → 读 .rels 前先查存在性。
- **控制台 GBK**：所有脚本 `sys.stdout.reconfigure(utf-8)`，否则中文/符号在 Windows 控制台报 UnicodeEncodeError（尤其 subprocess 子进程各自要 reconfigure）。
- **规则可现算**：层级/合格/复测这些派生列都能从库里 box_name/power 用 pt_rules 现算（`enrich`），**改规则不用重跑 OCR**——只有地址结构化和更准的标牌识别才需重跑 OCR。

## 数据模型（records 表关键字段）

| 字段 | 来源 | 说明 |
|---|---|---|
| source_file / sheet / row | 路径 | 唯一键（覆盖用） |
| box_name | 表格 | 盒子名（权威，干净） |
| power_dbm | 照片优先 | 功率幅值（正数） |
| lat / lon / address | 照片 | 定位/修 GPS 用 |
| test_date / pass_fail | 照片时间/表格 | — |
| conf / photo_status / power_check / box_check / has_coord / has_addr / main_issue | 校验 | 全枚举，可筛 |
| area / cluster_code / zone / hub / level / fat | 规则解析 box_name | 报表下钻维度 |
| fat_valid / power_status_ | 规则 | 命名合规 / 功率合格 |
| needs_retest / retest_reason / retest_priority | 规则 | 复测行动清单 |
| model / ocr_at | 运行 | 溯源 |

## 报表路线图（待做）

1. **盒子级去重复测清单**（V1.1 首要）：按 `area+zone+hub+level+fat` 归一，一盒一行，取最佳记录（有证据/最新）；盒子级 needs_retest = 该盒所有记录都无有效照片+功率证据。当前记录级会高估（无图汇总行重复）——实测 3816 唯一盒子里仅 643 有验证功率。
2. **覆盖率 & 未测清单**：和 kmz_toolkit 的 `fat_boxes.sqlite`（设计侧全量盒子）按盒子名 JOIN，算每站测了百分之几、列出设计有但从没测的盒子。
3. **趋势**：同盒子跨版本（V1 fail → V4 pass），看整改是否见效。
4. **质量追责**：按 uploader 统计无图判Pass/造假率。

## 待办（明确 V1.1/V2 范围）

- **盒子级去重报表**（纯 SQL，快）
- **地址结构化**：从照片 GPS 叠加拆出 Area(LGA) / Estate(封闭小区) / Street；无街名时取最近街名加 `Off` 前缀（例 `Off 13 Ogundimu St`）——需改 OCR prompt + 重跑。
- **倾斜/异焦平面照片增强**：功率计与二维码/标牌不在同一焦平面时读得更准——prompt 引导 + 必要时上 pro 复核硬样本。

## 补充说明

| 项 | 值 | 说明 |
|---|---|---|
| 数据源 | WeLink 云空间群空间「Power Test」xlsx（约 359 个，日增） | 下载由 onebox_downloader 负责 |
| 图片策略 | 临时目录 OCR、用完即删，绝不入库 | 信息安全硬约束 |
| 唯一键 | (source_file, sheet, row) | 覆盖重导幂等 |
| 盒子身份 | FAT 名归一化 `area+zone+hub+level+fat` | 去重/报表锚点 |
| 功率阈值 | -25 < v ≤ -10 合格 | 客观判定，不信人填 Pass |
| OCR 模型 | gemini-3.5-flash（直连，VPN 关） | 硬样本可上 gemini-3.1-pro 复核 |
| 环境变量 | GEMINI_API_KEY | Google 直连 key |

---

## V2.0 服务化

> 本迭代把 V1 本地处理管线改造成 C/S：客户端 CLI 复用现有全流程、把落库换成上传；服务端从零写，存图+存数据+查询 API。

### 架构（已定稿·方案A 客户端全栈）

```
客户端 CLI（手动触发，改造 pt_batch.py）:
  1. 开内网VPN → 下载 xlsx (onebox_downloader)        ← 复用
  2. 抽取 行+照片 (pt_extract)                          ← 复用
  3. 关VPN → OCR 照片 (pt_ocr, Gemini/Codex CLI)        ← 复用
  4. 规则校验+合并 (pt_merge/pt_rules)                  ← 复用
  5. 开内网VPN → 上传 结构化数据+原图 回服务端           ← 新增：pt_db 落库换成上传
服务端（V1 本机测试 → 生产上内网服务器）:
  接收上传 → 存图(磁盘) + 存数据(DB) + 查询API(数据+图)   ← 全新写
```

### 关键决策（记入 progress.md Decisions）

- **下载留客户端，放弃服务端自动下载**：权衡过 4 个架构方案（客户端全栈 / 服务端中枢+OCR worker / 服务端中枢+批量摆渡 / 服务端本地视觉模型）。服务端内网不能联外网做 OCR，图片必须到客户端 OCR；而内网 VPN 与外网互斥，"服务端下载→客户端 OCR"路线要跨端传图 + VPN 反复切，代价大于"下载留客户端"。选方案 A：图片从来在客户端、只客户端→服务端传一次，复用最大（5 步里 4 步原样），工程量最小。
- **OCR 留在客户端**：服务端不能联外网调 Gemini/Codex CLI，故 OCR 必须在客户端做（延续 V1 的 pt_ocr 双后端 gemini/claude/codex，走订阅额度不按量计费）。
- **图片入库约束修订**：V1"图片绝不进库"硬约束在 **V2.0 内网闭环、不外传**前提下解除——服务端可存原图。理由：服务端部署在内网（V1 本机测试，生产上内网服务器），不传外部；图片存磁盘 + DB 存路径，不入 blob（大图进 DB 是反模式）。

### 功能需求

**客户端 CLI 改造（pt_batch.py）**：
- 步骤 1-4 完全复用现有 pt_extract/pt_ocr/pt_merge/pt_rules，逻辑不动。
- 步骤 5 新增"上传"模块替代 pt_db 落库：把校验合并后的结构化记录 + 锚定原图，按 (source_file, sheet, row) 唯一键批量上传服务端，覆盖幂等（沿用 V1 唯一键语义）。
- **上传前先算 content_md5 + 文件大小 查增量**：客户端下载完 xlsx 算 MD5 和 size → 查 `GET /files` 返回的清单（本地缓存 + since 增量拉）→ 命中（同 MD5 + 同 size 已入库）则**跳过抽取/OCR/上传全流程**，省最贵的 OCR 算力。
- 客户端本地不再持久化 SQLite（pt_db 落库移除/降级为可选缓存）；权威数据在服务端。
- 手动触发，不引入 watcher/定时（V2.0 不做自动下载）。

**服务端（全新）**：

**去重/合并（核心，V1 踩过的重复坑服务端机制化）**——分两套，时机不同：

**A. 文件去重（入库时做，物化标记 duplicate_of）**：
- 权威键 = `content_md5 + file_size` 双校验（不靠文件名——V1 踩过同名不同内容 Dawaki 双版本；加 size 兜 xlsx 同内容不同字节的 zip 重打包漏网）。
- 上传时服务端查 `content_md5+file_size`：
  - 不存在 → 新入库（存图 + 存记录）
  - 同 source_file 同 md5+size → upsert 覆盖记录（记录级幂等，沿用 V1 唯一键 (source_file,sheet,row)）
  - 不同 source_file 同 md5+size → 标 `duplicate_of=原文件`、**只存记录不重存图**（图复用）、不自动删（误删风险留人工）
- 同 md5+size 的文件在文件去重层就跳过/标记，图只存一份（按 content_md5/sheet/row 组织），无"duplicate 复用图悬空"问题。

**B. 记录合并（查询时窗口函数算，不物化，沿用 V1 pt_report.py 思路——规则改了随时重算，避免与 records 失同步）**：
- 合并键 fallback 三档（V2.0 砍 L2 area 容错档，保守走 L1→L3→孤儿）：
  - **L1**：`area+cluster+zone+hub+level+fat` 严格相等（V1 `parse_fat` 归一化解析成功且键等）→ 合并
  - **L3**：`box_name` 原值清洗后相等（归一化解析失败但同写法，如缺前缀的 H#L#S# 一致）→ 合并
  - **孤儿**：解析失败且原值也不等 → 标 `box_key_missing` 留人工，**不强行并**（误并比留孤儿危险：把两个真盒子并成一个，有图赢家可能盖错对象，脏数据且查不出）
- 选赢家 ORDER BY：`evidence DESC, conf_rank ASC, ocr_at DESC`
  - `evidence=1` 当 `photo_status='有图清晰'`（**V2.0 放宽**：去掉 V1 的 `AND power_check IN('一致','表缺已恢复')`——图清晰即证据，功率以图为准，表格填错不影响图的可信度；功率不符不算图的问题）。模糊图/无图 evidence=0 同档。**evidence 仅用于记录合并选赢家排序，不影响图片存储——所有原图（含模糊图）一律存服务端，不因 OCR 不清晰而丢弃**（模糊图存档供后续模型升级重读或人工复核）。
  - `conf_rank`：高>中>低>无效（沿用 V1）
  - `ocr_at DESC`：最新优先
- 功率值：图清晰以图为准（V1 沿用，power_dbm 来自照片）；模糊图/无图退表格或标问题（power_check='图糊用表'）
- **L2 area 容错合并 V2.0 不做**：权衡后认定合并键来自表格 box_name（人填，area 是干净字母，无 OCR 数字混淆可兜），area 模糊匹配无可靠判据、误并风险高于收益；V1 命名合规率 98% 支撑 L1 严格键已覆盖大多数。留人工兜底。

- **上传接口**：`POST /files/{source_file}/records`（multipart，每批 ≤50 行/图，避免超限）。服务端按 A 做文件去重 + 记录 upsert 覆盖。原图落磁盘（按 content_md5/sheet/row 组织，同 md5 不重存），DB 存 image_path + content_md5 + file_size + duplicate_of + 结构化字段。覆盖时先写新图→更新 DB 路径→删旧图，事务保证。
- **查询 API（供 digifiber-conflation 下游拉）**：
  - `GET /records`：批量拉记录列表，筛 area/zone/hub/box_name/needs_retest/photo_status/conf，分页 page/page_size + 排序。记录合并按 B 在查询时用窗口函数算（不物化）。默认排除 `duplicate_of IS NOT NULL`（重复副本）和 `box_key_missing`（孤儿）；`?include_duplicates=1` / `?include_orphans=1` 显式看全。
  - `GET /records/{id}`：单条记录详情。
  - `GET /records/{id}/image`：按需取原图。
  - 数据 schema 沿用 V1 records 表字段（见「数据模型」章），新增 image_path；files 表加 content_md5/file_size/duplicate_of。
- **增量去重接口**：`GET /files?since=<ts>` 返回 `[{source_file, content_md5, file_size, record_count, duplicate_of, ocr_at}]`，供客户端上传前查增量跳过。
- **存储**：DB 沿用 SQLite + pt_db schema（V1 本机测试零迁移），files 表加 content_md5/file_size/duplicate_of、records 表加 image_path；图存磁盘目录。生产上内网服务器时再评估是否迁 PostgreSQL（V2.0 不做）。

### 待 V1 本机测试验证项（规则已定，边界待实测）
- `content_md5 + file_size` 双校验是否兜得住 xlsx 同内容不同字节的 zip 重打包漏网（极端"同内容同大小不同字节"需实测才知道）
- L3 box_name 原值相等的误并概率（两个不同盒子原值恰好同写法，概率极低但非零）

### 接口清单

| # | 接口 | 方法·路径 | 用途 | 必要性 |
|---|------|----------|------|--------|
| 1 | 健康检查 | `GET /health` | 运维探活 | ✅ |
| 2 | 已入库文件清单 | `GET /files?since=` | 客户端增量去重依据（含 content_md5/duplicate_of） | ✅ |
| 3 | 批量上传记录+图 | `POST /files/{source_file}/records` (multipart, 每批≤50) | 客户端上传一个文件的结果，服务端做文件去重(A)+记录合并(B) | ✅ |
| 4 | 记录列表查询 | `GET /records`（多维筛+分页+排序，默认排除重复副本） | 下游批量拉 | ✅ |
| 5 | 单条记录 | `GET /records/{id}` | 下游取详情 | ✅ |
| 6 | 取原图 | `GET /records/{id}/image` | 下游按需取图 | ✅ |
| 7 | 盒子级聚合 | `GET /boxes` | 盒子维度拉取 | ⚪可选（依赖 V1.1 盒子级去重，后置） |
| 8 | 导出 CSV | `GET /records/export` | 人导出 | ⚪可选 |
| 9 | 统计 | `GET /stats` | 覆盖率/分布 | ⚪可选 |

V2.0 做 #1-#6（✅），#7-#9 后置。认证 V1 本机测试无，生产内网再加。不做缩略图/Web 前端/WebSocket。

### 约束修订

- **图片存储（V2.0 解除 V1 禁令）**：服务端在内网闭环、不外传前提下可存原图。客户端临时图片用完即删（V1 习惯保留）；服务端原图持久化。**核心目的之一=OCR 溯源**：V1 图不入库用完删，OCR 结果（power/坐标/盒名/地址/时间）存了但图没了，业务方无法核对结果从哪张图来；V2.0 每条记录的 `image_path` 指向**产生该条 OCR 结果的那张原图**，下游/业务方可通过 `GET /records/{id}/image` 取原图核对 OCR 结果（V1 痛点：结果无图可溯）。所有原图（含模糊图）一律存，不丢弃——模糊图虽不作合并证据，但仍是该记录的溯源依据，且供后续模型升级重读或人工复核。
- **VPN 切换**：仍由客户端承担（开内网VPN 下载→关外网 OCR→开内网VPN 上传），V2.0 不解决 VPN 互斥、不引入同时通内外网要求。

### 技术方向（V2.0 增量）

| 维度 | 选择 | 理由 |
|---|---|---|
| 服务端框架 | FastAPI（Python） | 与现有脚本同语言；自带 OpenAPI 文档；异步上传图友好 |
| 服务端存储 | SQLite（沿用 pt_db schema）+ 图存磁盘 | V1 本机测试零迁移；图不入 blob |
| 客户端改造 | pt_batch.py 第 5 步落库换上传 | 复用最大，仅替换持久层出口 |
| OCR | 延续 pt_ocr 双后端（Gemini/Codex CLI） | 服务端不能联外网，OCR 必须在客户端 |
| 认证 | V1 本机测试无认证；生产内网再加 | 简单优先 |

### V2.0 明确不做

- 服务端自动下载（下载留客户端）
- 同时通内外网 / VPN 自动切换
- Web 前端 / 看板（API 只供 digifiber-conflation 程序化消费）
- 服务端 OCR（本地视觉模型）
- PostgreSQL 迁移（生产部署时再评估）
- 多用户/权限（V2.0 单消费方）
- **L2 area 容错合并**（合并键 fallback 只走 L1 严格 → L3 原值 → 孤儿；area 容错无可靠判据、误并风险高于收益，留人工兜底）
