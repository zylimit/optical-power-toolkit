# Product Spec — 光功率测试数据处理系统（optical-power-toolkit）

> 版本：V1（Excel 照片 → 结构化数据库 → 复测清单/报表）
> 日期：2026-07-10 · 从 web-control 的 PT 处理专题独立成工程
> 迭代原则：小步。本 Spec 覆盖已建成的处理管线 + 数据模型 + 规则引擎，报表/去重/地址结构化在后续迭代扩展。
>
> **状态分期**：
> - **V1.0（已建成）**：xlsx 抽取（表+锚定照片）、Gemini 视觉 OCR、表图交叉校验、FAT 命名规则引擎、功率合格判定、SQLite 入库（增量/覆盖/询问三模式）、派生指标（层级/复测清单）、CSV 导出。
> - **V1.1（待做）**：盒子级去重报表（管理者复测清单）、地址结构化（Area/Estate/Street + Off 规则）、倾斜/异焦平面照片 OCR 增强。
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
- 图片入库（**信息安全硬约束：图片绝不进库**）

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
