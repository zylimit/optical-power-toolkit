# Project: optical-power-toolkit
_Last updated: 2026-07-10_

## Pinned（仅高置信"必须遵守"写入；受保护不可修订）
    - 图片绝不入库（信息安全硬约束）：DB 只存文本/数值，图片仅本地临时目录过一次 OCR，每个文件处理完立即删
    - VPN 状态相反：下载 xlsx 要开内网 VPN（onebox 内网），OCR 调 Gemini 要关 VPN（外网），两阶段不能同时
    - 合并键/OCR 结果文件名必须带 source_file：多文件常有相同 sheet 名，不带 source_file 会跨文件同行号覆盖（踩过一次，盒子误报从 189 降到 16）
    - 唯一键 (source_file, sheet, row)：重复即覆盖，覆盖重导幂等
    - 功率合格阈值：-25 < value ≤ -10（客观判定，不信人填 Pass）
    - 真相优先级：功率/坐标/地址以照片为准，盒子名以表格为准
    - 所有脚本 sys.stdout.reconfigure(utf-8)（Windows 控制台 GBK 坑，subprocess 子进程各自要 reconfigure）
    - V1 明确不做：KMZ 设计侧处理、Web UI、图片入库

## Decisions（按时间顺序追加，历史不可改）
    - 2026-07-10: 从 web-control 的 PT 处理专题独立成工程 optical-power-toolkit（理由：光功率处理管线已自成体系，与 web-control 主线解耦）
    - 2026-07-10: 存储选 SQLite 单文件（理由：十万级记录足够、可拷贝、零服务器）
    - 2026-07-10: OCR 选 gemini-3.5-flash REST 直连（理由：绕开内网推理网关超时；硬样本可换 gemini-3.1-pro 复核）
    - 2026-07-10: 除视觉 OCR 外全用确定性规则，不引入 AI（理由：规则可解释、可审计、零成本）
    - 2026-07-10: 真相优先级定为功率/坐标/地址以照片为准，盒子名以表格为准（理由：实测功率读得 100%、坐标 85%、地址 60%，盒子名标牌整串 OCR 噪声大而表格人填干净）
    - 2026-07-10: OCR 复核模型落地为 gemini-3.1-pro-preview（理由：WebSearch 验证 2026-07 pro 系列无 plain gemini-3.1-pro id，只有 preview；Spec 中"gemini-3.1-pro"是简写，实现按 preview id 走，pt_recheck 的 --model 做成参数防 preview 改版）
    - 2026-07-10: Phase 2/3 的 prompt 变更共用一次全库 OCR 重跑（理由：全量 Gemini 调用贵，Phase 2 只做单文件验证，v3 prompt 定稿后 pt_batch --mode refresh 一次生效两个 Phase 的变更）
    - 2026-07-10: 盒子级报表不建物化表、查询时导 CSV（理由：规则改了随时重导，避免与 records 失同步）

## TODO（权威待办清单）
    - [P1][OPEN][#2] 地址结构化：从照片 GPS 叠加拆出 Area(LGA)/Estate/Street，无街名取最近街名加 Off 前缀（需改 OCR prompt + 重跑 OCR）（Context：Product-Spec.md 待办，DEV-PLAN.md Phase 2 addr_area/addr_estate/addr_street）
    - [P2][OPEN][#3] 倾斜/异焦平面照片 OCR 增强（prompt 引导 + 必要时上 gemini-3.1-pro-preview 复核硬样本）（Context：Product-Spec.md 待办，DEV-PLAN.md Phase 3 pt_recheck.py）

## In Progress
    （无）

## Done（最近完成的放前面）
    - 2026-07-10: [V1.1 Phase 1][#1] 盒子级去重复测清单完成——scripts/pt_report.py（175 行，boxes 子命令：窗口函数选最佳记录 + 盒子级复测判定 + boxes/unparsed 双 CSV + --where 筛选）+ tests/test_pt_report.py（18 例回归全绿）。四步走验证通过（review 双 Task PASS / unittest 18 OK / py_compile 全过 / 真实库功能+回归 OK）（evidence：commits dbf15d91, eab00be7, fdf4fecc；真实库跑出唯一盒子 4783、有证据 611、需复测 4202）
    - 2026-07-10: [V1.1] DEV-PLAN.md 已生成并通过 plan-lint（3 个 Phase：①盒子级去重复测清单 pt_report.py ②地址结构化 addr_area/addr_estate/addr_street + Off 规则 ③倾斜照片 OCR 增强 + pt_recheck.py 硬样本 pro 复核）（evidence：DEV-PLAN.md，plan-lint exit=0）
    - 2026-07-10: [V1.0] 下载 onebox_downloader（WeLink 云空间，Playwright + SSO 会话，断点续传、失败重试）（evidence：scripts/onebox_downloader/, scripts/onebox_download.py）
    - 2026-07-10: [V1.0] 批处理编排 pt_batch.py（增量 skip / 覆盖 refresh / 询问 ask 三模式，0 行文件登记跳过）（evidence：scripts/pt_batch.py）
    - 2026-07-10: [V1.0] 交叉校验入库 pt_merge.py + pt_db.py（SQLite，UNIQUE(source_file, sheet, row) 覆盖幂等，files/records 双表 + 多索引）（evidence：scripts/pt_merge.py, scripts/pt_db.py）
    - 2026-07-10: [V1.0] Gemini 视觉 OCR pt_ocr.py（gemini-3.5-flash 直连、缩图 1024、断点续传、5 次重试+退避）（evidence：scripts/pt_ocr.py）
    - 2026-07-10: [V1.0] xlsx 抽取 pt_extract.py（光功率 sheet 识别、列自适应、照片按行锚定 xdr:from + drawing rels）（evidence：scripts/pt_extract.py）
    - 2026-07-10: [V1.0] 规则引擎 pt_rules.py（FAT 命名解析、功率合格判定、复测清单派生，enrich 可现算无需重跑 OCR）（evidence：scripts/pt_rules.py）

## Risks & Assumptions
    - Risk：现场数据极脏（多 sheet 混杂、命名不统一、漏填/填错甚至无图判 Pass 造假），人工核对上万条不现实（Mitigation：照片+表格交叉校验 + 全枚举置信度/问题分类）
    - Risk：功率列偶尔混进日期序列号（Excel 日期如 45883）（Mitigation：合并时做 sanity 检查，超出 dBm 幅度 >60 视为无效退用照片值）
    - Assumption：FAT 命名合规率 98%、area 覆盖率 98.4%（Confidence：High，来自实测）
    - Assumption：照片信息可信度——功率读得 100%、坐标 85%、地址 60%（Confidence：Med，来自实测，作为真相优先级判定依据）

## Notes（简要要点）
    - 2026-07-10: 真实库现状基线（D:/Code/web-control/pt_data.sqlite）：records 8862 / files 24 / 唯一盒子 4783 / 有验证证据盒子 611 / unparsed 141——Spec 里的 3816/643 是早期快照，验收以现库实数为准
    - 2026-07-10: box_key 不带 Z 段的有 1044 个（省 Z 变体观察值），Phase 1 不做跨键合并，问题显著再迭代
    - 2026-07-10: D:\Code 大仓库无 git remote，所有 commit 仅在本地
    - 2026-07-10: 项目定位——digifiber-conflation 修正体系里的光功率修正源生产端，本工具只负责把散乱 PT Excel 处理成干净可查库，不做地图/放号
    - 2026-07-10: V2 北极星方向——与 digifiber-conflation 打通，用光功率坐标修 NCE 的 GPS 漂移、按 FAT 名和设计库对覆盖率（尚未启动，暂不建 TODO）
    - 2026-07-10: OCR 结果文件名清洗正则曾写错 r'\w'，导致所有 sheet 名清洗成同一个 `_`、文件名撞车、只写了一半还误判"全跑完"——命名清洗正则要测（历史踩坑，已修复）
    - 2026-07-10: 空 drawing 无 .rels 会让照片锚定崩溃，已修复为读 .rels 前先查存在性

## Context Index（轻量索引）
    - Archive：./progress.archive.md（尚未创建）
