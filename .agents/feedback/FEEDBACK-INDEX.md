# Feedback Index

> 经验教训索引。新建或更新 feedback 文件后，同步更新此索引。
> 格式：每条一行，`- [标题](文件名.md) — 一句话描述`
> 模板：templates/feedback-topic-template.md

- ✅[已毕业] [主 Agent 职责边界：编码/审查/测试/部署一律委派专职 Sub-Agent](main-agent-no-direct-coding.md) — 主 Agent 只「写提示词 + 验收」，不亲自写代码/审查/测试/部署；环节→派发目标（Codex Sub-Agent）：编码=implementer、审查=code-reviewer、测试=tester（写测≠被测作者）、部署=deployer；文档类工作不在此约束内
- ✅[已毕业] [部署验收：独立核查三件套，不轻信子 Agent 回复状态](deploy-acceptance-independent-verification.md) — 子 Agent incomplete/空回复/自报通过 ≠ 部署结果；验收以宿主真实状态为准：容器创建时间戳+镜像tag（勿用 Up 时长）/ 健康检查端点 / live 冒烟验证新功能产物；收尾清理临时产物+确认版本文件已提交
- ✅[已毕业] [测试独立性：写测者 ≠ 被测代码作者](test-independence-author-not-tester.md) — 自码自测易"作弊"（confirmation bias），把作者错误假设原样写进断言；测试派独立方：tester Sub-Agent 或非作者的另一 implementer fresh 实例；主 Agent 不写测试且独立复核运行输出
- ✅[已毕业] [测试卡点：测试通过是打包/交付前的强制前置闸门](test-gate-before-packaging-delivery.md) — "部署/打包"指令不豁免测试；打包前必须有相关功能回归测试已运行且通过的证据（含重跑已有套件），带后端逻辑变更的版本新功能须补回归测试或手动功能验证证据，卡点未过不许进入打包→交付；可让 release-builder 复用 test-builder（派 tester）作前置闸门
- ✅[已毕业] [多 repo 提交隔离：独立 repo 各自提交，禁止耦合进同一脚本](multi-repo-commit-isolation.md) — 多个独立 git repo 的 add/commit/push 必须分开、逐个独立执行并各自验收远程同步状态，不得为省事耦合进同一脚本；归属/认证不同（ssh vs https）时耦合会掩盖单点失败造成"半成功"烂局
- [recap/clear/session 恢复必须贯彻三文件同步恢复铁律](recap-three-file-recovery.md) — recap/clear/session 恢复时主 Agent 必须同步读取 progress.md、Product-Spec.md、Product-Spec-CHANGELOG.md，并明确缺失/降级项，不能只做仓库状态检测或让 progress-recorder 代替主控恢复
- [仓库刷新应遵循用户明确授权，避免擅自加重流程](repository-refresh-follow-explicit-scope.md) — 用户已允许清理并要求直接拉取时走最短安全路径；不擅自增加临时克隆、比对、备份交换，也不保留已明确不要的旧资产
- [脚手架开发遵循用户明确的质量门禁豁免](scaffold-development-skip-quality-gates.md) — 区分“开发脚手架内核”和“用脚手架开发业务项目”：本轮维护可按用户要求跳过 review/test 门禁，但不得删减最终脚手架的审查、测试和用例能力
- [调研使用 Codex 原生 Sub-Agent，主 Agent 保留独立判断](native-subagent-research-main-agent-judgment.md) — 长目录和复杂材料学习不用本地 Gemini/ask 桥，优先派 Codex 原生 fresh Sub-Agent；主 Agent 仍须亲读关键材料、核对证据并独立判断改进点
- [研究下钻按指定递归深度执行，不能用平级数量冒充深度](recursive-research-depth-not-fanout.md) — 用户要求向下多打 N 层时，按指定深度逐层递归；每层须有独立研究边界、证据路径、共识/分歧与上层验证，不能用同层 fan-out 冒充深度
- [脚手架交付应复制即用且保持项目根目录清爽](copy-ready-clean-scaffold-layout.md) — 核心 `.codex`、`.agents`、`AGENTS.md` 应可直接复制使用；安装器仅作可选便利工具，维护资产收进隐藏目录或留在源仓库
- [快速开发模式应显式、限时且保留安全护栏](fast-mode-explicit-temporary-quality-bypass.md) — Fast Mode 默认关闭，支持 on/off/status 和 24 小时自动过期；临时跳过测试、检视、用例及质量门禁，但安全护栏始终生效
- [仓库清爽不等于去品牌化，清理时默认保留品牌识别资产](preserve-brand-assets-during-cleanup.md) — 清理、精简或迁移脚手架前先盘点 Logo、Banner、初始化话术和项目名视觉等品牌资产；默认保留，删除或重做须经用户明确同意
- [配置型重复故障应扫描全部同类实例](bug-fix-scan-sibling-config-instances.md) — 修复重复 Hook/阈值/wrapper 配置时扫描全部同类项，完成声明须覆盖故障类别而非单个实例
