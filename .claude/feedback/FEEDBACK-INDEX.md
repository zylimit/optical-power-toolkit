# Feedback Index

> 经验教训索引。新建或更新 feedback 文件后，同步更新此索引。
> 格式：每条一行，`- [标题](文件名.md) — 一句话描述`
> 模板：templates/feedback-topic-template.md

- [hook 解释器选 pwsh 7，不用 Windows PowerShell 5.1](hook-interpreter-use-pwsh7-not-powershell51.md) — 本机配置 hook / 脚本解释器时 PowerShell 一律用 pwsh 7 绝对路径（含空格加引号、bash 命令串用正斜杠），其余裸 powershell.exe hook 超时时按同法逐个替换
- [生产删除前重查目标当前状态，归因须有直接证据](destructive-ops-recheck-live-state-and-require-direct-evidence.md) — 生产/共享环境的删除・停用・覆盖类写操作，执行前当场重查目标最新状态、归因要直接证据（旧快照 + 时间推断不作数）；误删用户在跑的导入 Session 的实害教训
- [大模型调用优先用订阅/OAuth 额度的 CLI，禁止按量计费 API](llm-calls-use-subscription-cli-quota-never-metered-api.md) — 接入任何大模型能力（OCR/生成/审查）前先确认计费路径：优先用户已登录的订阅额度 CLI（如 gemini oauth-personal），禁按量计费 REST API / apikey 模式 CLI；计费模式须先与用户对齐替代方案
- [长跑批处理须有看门狗与输入预检，发现挂死立即止损不观望](long-batch-needs-watchdog-input-precheck-and-prompt-stop-loss.md) — 批处理流水设计期默认加超时看门狗（超时即杀 + 隔离病态文件）、输入侧廉价预检（如 sheet 超 7 万行直接跳过）；主 Agent 监控后台任务确认挂死即报告并止损，不许"进程还活着"式观望；病态 Excel 空转 4 小时的实害教训
