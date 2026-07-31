# optical-power-toolkit

FTTH 光功率测试数据处理工具集：从 WeLink 云空间**下载** PT Excel → **抽取**表格+照片 → **Gemini 视觉 OCR** → 表图**交叉校验** + 规则清洗 → **SQLite 入库** → 随时**导出报表**。

自包含，整目录拷走即用。详细设计见 `docs/Product-Spec.md`。

## 环境

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   /   Linux: source .venv/bin/activate
pip install -r requirements.txt
# 开发/自动化测试额外依赖（包含 pytest）
pip install -r requirements-dev.txt
playwright install chromium          # 仅下载阶段需要
```
- **OCR 直连 Gemini**：设置环境变量 `GEMINI_API_KEY`（Google API key）。
- **VPN 状态相反**：下载要**开**内网 VPN（onebox 内网）；OCR 要**关** VPN（Gemini 外网）。

## 目录

```
optical-power-toolkit/
  scripts/
    onebox_downloader/     # 下载器包
    onebox_download.py     # 下载入口
    pt_extract.py          # ① xlsx → 行+锚定照片（认光功率sheet、列自适应）
    pt_ocr.py              # ② Gemini OCR（直连、缩图、并发、断点续传、重试）
    pt_merge.py            # ③ 表图交叉校验 → 枚举列
    pt_rules.py            # FAT命名规则引擎 + 功率合格判定 + 复测决策
    pt_db.py               # SQLite 存储层（files/records 表、导出、enrich）
    pt_batch.py            # 主编排：增量、临时目录用完即删、三模式
    pt_pipeline.py         # 单次跑通（extract→ocr→merge→csv，出 CSV 不入库）
  docs/Product-Spec.md     # 完整规格
  requirements.txt
  requirements-dev.txt     # 开发和自动化测试依赖
```

## 用法

### 1. 下载（开 VPN）
```bash
cd scripts
python onebox_download.py --login          # 弹浏览器 SSO，刷新会话
python onebox_download.py --workers 3      # 下全部 xlsx 到 onebox_power_test/，断点续传
```

### 2. 处理入库（关 VPN，Gemini 直连）
```bash
# 增量：扫全目录，已入库的跳过，一批吃 10 个
python pt_batch.py onebox_power_test --db pt_data.sqlite --mode skip --limit 10
# 全部重跑覆盖：--mode refresh
```
逐文件流水：抽取 → 临时目录 OCR → 入库 → **删临时图片** → 下一个。图片绝不进库。

### 3. 导出报表（随时）
```bash
python pt_batch.py --db pt_data.sqlite --export-only 总表.csv
python pt_batch.py --db pt_data.sqlite --export-only 复测清单.csv --where "needs_retest='是' and retest_priority='高'"
python pt_batch.py --db pt_data.sqlite --export-only 高置信.csv --where "conf='高'"
```

### 4. 改规则/阈值后重算（不重跑 OCR）
```bash
python pt_db.py enrich --db pt_data.sqlite   # 用 pt_rules 重算 层级/合格/复测 派生列
```

## 数据库

`pt_data.sqlite` 两张表：
- **files**：每个扫过的 xlsx（记录数/图数/模型/时间）——增量去重靠它
- **records**：每条记录（盒子名/功率/经纬度/地址 + 全枚举校验列 + 规则派生列），`UNIQUE(source_file,sheet,row)` 覆盖

关键枚举列（可直接 SQL 筛）：`conf`(置信度) · `main_issue`(主要问题) · `power_status_`(功率合格) · `needs_retest`/`retest_reason`/`retest_priority`(复测) · `area`/`zone`/`hub`/`level`/`fat`(层级)。

## 已知待办（见 Spec）
- 盒子级去重复测清单（记录级会因无图汇总行高估）
- 地址结构化（Area/Estate/Street + `Off` 规则）
- 倾斜/异焦平面照片 OCR 增强
