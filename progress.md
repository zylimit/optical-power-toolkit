# Project: optical-power-toolkit
_Last updated: 2026-07-11_

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
    - 2026-07-10: optical-power-toolkit 接入独立 GitHub 远端 https://github.com/zylimit/optical-power-toolkit（当时是空仓库）（理由：D:\Code 是大仓库根、无独立 .git，本项目只是其子文件夹，无法直接 git push；用 `git subtree split --prefix=optical-power-toolkit -b optical-power-toolkit-split` 在 D:\Code 顶层拆出只含本项目路径的干净历史分支，push 到该远端 main 分支，4 个已有 commit（V1.0 基线补提交 + Phase 1 三个 commit）历史完整保留；已在 D:\Code 顶层加了名为 `optical-power-toolkit-origin` 的 remote——**注意**：该 remote 挂在 D:\Code 顶层，不在本项目子目录内，以后推送新 commit 仍需在 D:\Code 顶层重新跑一次 subtree split 再 push 该分支，不能直接在本子目录里 git push）
    - 2026-07-11: M2 缺陷（非硬样本行 OCR json 补齐失败时被静默降级、且行数不变导致旧的净行数对比检测不到）修复方向选定"预防+检测两层"而非纯 detection-only（理由：三个回归测试在共享同一"row2 json 漏落地"fixture 时相互矛盾——store 前完整性校验要求跑完 vs row2 完全不受影响 vs 劣化需真实发生且被捕获，detection-only 设计下三者不可能同时满足；已用 AskUserQuestion 征询，用户选择"预防+检测两层（推荐）"）。最终实现：预防层用 pt_merge.load_ocr() 同源校验 OCR json 完整性，缺行即 RuntimeError 中止该文件（库内保持原样）；检测层 diff_stats() 的全量扫描范围保留，作为内容级劣化（json 落地但内容本身劣化，如 rebuild_json 字段映射 bug）的兜底捕获，两层互补非二选一；三个回归测试相应拆分为独立场景（各自独立 fixture，不再共享同一破坏性 mock）
    - 2026-07-11: code-reviewer 越权创建测试文件的处置——本轮 Phase 3 code-review 派单中，code-reviewer Sub-Agent（agent a6965510fa6f2b14d）越权创建了 tests/test_pt_recheck_units.py（审查者应只审查+报告，不应编码/写测试，违反角色边界）。主 Agent 独立核验该文件内容（逐条断言对照 scripts/pt_recheck.py 源码：fetch_hard 三条件筛选、diff_stats 三项计数、process_file 临时目录清理两条路径），确认测试真实、针对性强、非空转；且编写者（code-reviewer）与被测代码作者（Task 3.2 的 implementer）不是同一身份，不存在自证式确认偏误。fresh 跑 `python -m pytest tests/test_pt_recheck_units.py -v` 6 passed。**决定：保留该文件**，但记录角色越界事件本身作为需关注的流程偏差，不代表默许审查者今后可以顺手写代码
    - 2026-07-11: Sub-Agent 回传系统性退化问题（未根治，需后续关注）——本轮 Phase 3 四步走验证中，先后派发的 tester（两次：一次 resume 一次 fresh）和 code-reviewer（fresh 重派 a2a12537349f38310）均只回传裸结论（"PASS."/"已闭环。"），无任何证据，且 resume 追问后进一步退化（"."、"无新增内容"）。三次独立复现（跨两种 agent 类型），判断为该环境下 Sub-Agent 最终回传消息存在系统性过度简略倾向，非单次会话污染偶发问题。应对：本轮 Phase 3 四步走的全部四项证据改由主 Agent 亲自跑命令/亲读源码独立取得（pytest 全量回归、单测试文件、py_compile、DEV-PLAN 交付清单逐条源码核对），未采信任何 Sub-Agent 自报结论。后续派单如再复现同样模式，不应再单纯"重派 fresh 实例"期待自愈，应考虑改用更强约束（如要求先证据后结论的固定格式，或主 Agent 直接跑验证命令为主、Sub-Agent 只做定位式辅助）

## TODO（权威待办清单）
    （无）

## In Progress
    （无）

## Done（最近完成的放前面）
    - 2026-07-11: [V1.1 Phase 3] 四步走完成验证——全部通过，证据均为主 Agent 当场亲自取得：Code Review——Task 3.1（scripts/pt_ocr.py PROMPT v3）逐条核对，焦平面引导/逐字符辨认/禁止脑补三要素齐全（对应 pt_ocr.py 39-42 行）；Task 3.2（scripts/pt_recheck.py）逐条核对 fetch_hard/process_file/diff_stats/main 均匹配 DEV-PLAN 交付清单与命令行参数规格，复用 pt_extract/pt_ocr.ocr_one/pt_db.store 未复制逻辑；2 个 Low 级发现（pt_recheck.py:161 shutil.rmtree(ignore_errors=True) 静默吞清理失败、pt_recheck.py:70-75 ocr_targets 对库外新行的兜底 OCR）均评估为非阻塞 / 测试完整性——`pytest tests/ -q` 全量 50 passed；`test_pt_recheck_units.py` 单独 6 passed / 编译验证——`python -m py_compile` 覆盖 pt_recheck.py/pt_ocr.py/pt_db.py/pt_merge.py/pt_extract.py/pt_pipeline.py/pt_rules.py 全部零错误 / 功能测试——复用 Task 3.3 已有的真实数据单文件复核验证证据（model/ocr_at 精确分层、M2 两层防线未误触发、临时目录清理、legible 不劣化、L1 遗留验收缺口已闭环）。Phase 3 是 DEV-PLAN.md 最后一个 Phase，四步走全部通过，等待用户确认 Phase 完成（evidence：本次核验为本地运行取证，无新增 commit）
    - 2026-07-11: [V1.1 Phase 3][Task 3.3] pt_recheck.py 真实数据单文件复核验证——用 Data/onebox_power_test/ 目录下真实文件 `13A Obafemi Anibaba FAT EXTENSION HP PT NOV6.xlsx`（该目录共 362 个真实文件，本次只取 1 个做最小化验证，未做全量批处理，未重新下载任何数据）：在项目根目录新建 pt_data.sqlite，先用 `pt_batch.py --limit 1` 初始入库 34 条记录（model=gemini-3.5-flash），再用 `pt_recheck.py --limit 1` 复核。结果全部通过：硬样本识别 row 19/20/28 共 3 行→model=gemini-3.1-pro-preview + 新鲜 ocr_at，其余 31 行 model/ocr_at 完全未变，验证"只重跑硬样本、不动其他行"设计生效；M2 防护层（完整性检查）未触发 RuntimeError，34 行全部正常入库无缺行；legible(有图清晰) 计数复核前后均为 25，未降级；M2 检测层（worse 扫描）未报告任何 worse 项；临时目录清理正常（系统 temp 无 pt_recheck_* 残留）；此前遗留的 L1 发现（has_coord=是但 lat/lon 为空）复查为 0 行，闭环。至此 TODO #3（倾斜/异焦平面照片 OCR 增强）Task 3.1/3.2/3.3 全部完成，Task 3.1 遗留的"无样本照片单发对比 v2/v3"验收缺口已由 Task 3.3 真实照片验证间接闭环，本次未观察到整串标牌脑补现象（evidence：本地验证运行，pt_data.sqlite + pt_recheck.py --limit 1 输出，无代码变更故无新 commit）
    - 2026-07-11: [V1.1 Phase 3][Task 3.2] pt_recheck.py 硬样本复核流水 + M2 静默劣化两层防线——新增 scripts/pt_recheck.py（237行：硬样本行（有图模糊/OCR失败/标牌糊未核对）重抽照片、只对硬样本跑 pro 模型 OCR，非硬样本行从库内字段反查重建近似 json 补齐，整文件重建入库）+ tests/test_pt_recheck.py（186行，M2 缺陷三场景独立回归锁）；修改 scripts/pt_ocr.py（result_path 从 main() 内部闭包提升为模块级函数，供 pt_recheck.py 复用）。code-reviewer 三阶段审查全通过（Stage 0/1/2），0 High/Medium，1 Low（非阻塞可选项），审查中用变异测试验证了两层防线确实有效（分别对预防层校验条件和检测层 before 扫描范围做变异，回归测试均按预期变红）。全量回归 44 passed（evidence：commit ceef94e2）
    - 2026-07-10: [V1.1 Phase 3][Task 3.1] scripts/pt_ocr.py PROMPT v2→v3：追加倾斜/异焦平面引导（功率计 LCD 与盒子标牌不同焦平面时分别就近取清晰读数互不牵连；倾斜标牌逐字符辨认，任一字符不确定即 legible=false，禁止按 FAT 命名格式脑补）。code-reviewer 三阶段全过（Stage0 静态闸 PASS：py_compile + pytest 41 passed；Stage1 规格合规 PASS；Stage2 代码质量 PASS）（evidence：commit c52f01fc）
    - 2026-07-10: [V1.1 Phase 2][#2] 地址结构化：从照片 GPS 叠加拆出 Area(LGA)/Estate/Street，无街名取最近街名加 Off 前缀（原 TODO #2 描述，需改 OCR prompt + 重跑 OCR）——四步走验证全过：Code Review 逐 Task 通过 / 测试完整性——新增 tests/test_pt_address.py（23 例，独立于实现者由 tester Sub-Agent 编写，覆盖 standardize_street 边界、reconcile 地址三段透传+孤立 Off 判定+near_street 类型容错、pt_db 建表与旧库迁移、build_records 写入路径），与 Phase 1 的 18 例合计 41 例全绿 / 编译验证——py_compile 对 pt_ocr.py+pt_rules.py+pt_merge.py+pt_db.py 四文件一起过 / 功能测试——即 Task 2.4 的真实 VPN-off Gemini 调用（evidence：commit 5f99f854）
    - 2026-07-10: [V1.1 Phase 2][Task 2.4] 验证性端到端跑通（无新代码）——选真实文件 `Adeniran Ogunsanya cluster_PT_18022026V6_(COMPLETED).xlsx`（129 图）关 VPN 跑 `pt_batch.py --mode refresh --limit 1` 验证：173 条记录入库，addr_street 非空率 71.7%（124/173，同量级于预期的 ~60% 照片地址可读率），临时目录确认自动清理，库总记录数不变（8862，覆盖幂等验证通过）
    - 2026-07-10: [V1.1 Phase 2][Task 2.3] scripts/pt_merge.py + scripts/pt_db.py：地址三段（area/estate/street）从 reconcile() 透传到 pt_db 落库，BASE_COLS/SCHEMA 新增三列，connect() 补齐旧库迁移（ALTER TABLE 幂等补列）（evidence：commit d1fb409b）
    - 2026-07-10: [V1.1 Phase 2][Task 2.2] scripts/pt_rules.py：standardize_street(street, near_street) 完整实现——空/无街名标记返回空串，near_street=True 加 "Off " 前缀，已有 Off 前缀不重复加
    - 2026-07-10: [V1.1 Phase 2][Task 2.1] scripts/pt_ocr.py：OCR prompt v2 新增 addr_area/addr_estate/addr_street/near_street 4 个返回字段
    - 2026-07-10: [housekeeping] 补提交 V1.0 全部基线代码与规划文档入 git——此前只有 Phase 1 的 pt_report.py/test_pt_report.py 被跟踪，其余 V1.0 脚本（pt_extract/pt_ocr/pt_merge/pt_db/pt_batch/pt_rules/onebox_download 等）和规划文档（README/requirements/Product-Spec/DEV-PLAN/.claude/）此前从未 git add 过，已按 .gitignore 核对无密钥后一次性提交（evidence：commit ef7b892b，114 files/9947 insertions）
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
    - 2026-07-10: 测试运行中发现 pt_merge.py:64 的 load_ocr() 有未关闭文件句柄的 ResourceWarning（open() 没用 with/上下文管理器）——属于 Phase 2 之前就存在的既有代码，本次未改动、非本 Phase 引入的回归，暂不处理，仅记录以备后续顺手清理
    - 2026-07-10: 测试中锁定一个边界行为：near_street 若被传入非空字符串杂质值（如 "false"/"no"），因 bool(x or False) 语义会被当作 True 处理并错误加上 "Off " 前缀——目前 Gemini OCR 返回的是真实 JSON boolean，此路径理论存在但生产不会触发，code review 判定 Low 优先级不阻塞，未在 Task 2.3 中修复
    - 2026-07-10: 真实库现状基线（D:/Code/web-control/pt_data.sqlite）：records 8862 / files 24 / 唯一盒子 4783 / 有验证证据盒子 611 / unparsed 141——Spec 里的 3816/643 是早期快照，验收以现库实数为准
    - 2026-07-10: box_key 不带 Z 段的有 1044 个（省 Z 变体观察值），Phase 1 不做跨键合并，问题显著再迭代
    - 2026-07-10: D:\Code 大仓库无 git remote，所有 commit 仅在本地
    - 2026-07-10: 项目定位——digifiber-conflation 修正体系里的光功率修正源生产端，本工具只负责把散乱 PT Excel 处理成干净可查库，不做地图/放号
    - 2026-07-10: V2 北极星方向——与 digifiber-conflation 打通，用光功率坐标修 NCE 的 GPS 漂移、按 FAT 名和设计库对覆盖率（尚未启动，暂不建 TODO）
    - 2026-07-10: OCR 结果文件名清洗正则曾写错 r'\w'，导致所有 sheet 名清洗成同一个 `_`、文件名撞车、只写了一半还误判"全跑完"——命名清洗正则要测（历史踩坑，已修复）
    - 2026-07-10: 空 drawing 无 .rels 会让照片锚定崩溃，已修复为读 .rels 前先查存在性
    - 2026-07-10: 发现 D:\Code\web-control\optical-power-toolkit\ 有一份陈旧重复目录（.gitignore/Product-Spec.md/README.md/requirements.txt/scripts/），diff 核实内容为项目独立拆分前的旧快照（scripts/ 与当前项目字节级相同但缺 pt_report.py，即 Phase 1 之前的版本），无独立 .git、未推送到任何远端，判断是项目拆分时遗留的过渡副本。未删除（存量资产保留复用铁律，删除需用户拍板），暂标记为待用户确认是否清理，不影响当前开发
    - 2026-07-11: Task 3.3 真实数据复核中，row 19/20/28 这 3 个硬样本经 pro 模型（gemini-3.1-pro-preview）复核后 photo_status 从"有图模糊"变成了"OCR失败"（未能升级为"有图清晰"），box_check 仍是"标牌糊未核对"——这是真实数据下 pro 模型对特定模糊照片确实无法提取结构化结果的真实结果，非代码缺陷。diff_stats() 的 worse 判定只覆盖"有图清晰→非清晰"和"通过→非通过"两类退化，"有图模糊→OCR失败"这种同属非清晰态之间的转换不在覆盖范围内（code-reviewer 审查 M2 时已作为 Low 级发现指出，属 Spec 边界内不算漏洞）。全量复核跑起来后，这类"复核未能改善"的行需要人工二次确认（如换角度重拍），不应自动判定为程序异常
    - 2026-07-11: scripts/pt_batch.py 存在 cwd 相关的相对路径问题——args.tmp 默认值 "pt_tmp" 按调用者 cwd 解析，但 OCR 子进程用 subprocess.run(cwd=HERE)（HERE 固定为 scripts/ 目录）启动，若从 scripts/ 之外目录（如项目根目录）调用会导致 --rows 相对路径解析基准错位、FileNotFoundError。当前规避方式：调用 pt_batch.py/pt_recheck.py 一律先 cd 进 scripts/ 目录，用 ../ 相对路径传 --db/--dir。属既有 V1.0 基础设施遗留问题，Task 3.2/3.3 范围外未修复，只是绕过；后续如需修，建议把 args.tmp 解析改为绝对路径锚定到 HERE 而非调用者 cwd
    - 2026-07-11: Phase 3 完成后全库 v3 复核的标准命令（须在 scripts/ 目录下执行，避免上述 cwd 路径问题）：`cd scripts && python pt_batch.py ../Data/onebox_power_test --db ../pt_data.sqlite --mode refresh && python pt_recheck.py --db ../pt_data.sqlite --dir ../Data/onebox_power_test`（若从项目根目录直接调用，--tmp 指向的临时目录会与 OCR 子进程 cwd 不一致而失败）

## Context Index（轻量索引）
    - Archive：./progress.archive.md（尚未创建）
