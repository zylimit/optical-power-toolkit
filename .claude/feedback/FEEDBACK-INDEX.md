# Feedback Index

> 经验教训索引。新建或更新 feedback 文件后，同步更新此索引。
> 格式：每条一行，`- [标题](文件名.md) — 一句话描述`
> 模板：templates/feedback-topic-template.md

- [hook 解释器选 pwsh 7，不用 Windows PowerShell 5.1](hook-interpreter-use-pwsh7-not-powershell51.md) — 本机配置 hook / 脚本解释器时 PowerShell 一律用 pwsh 7 绝对路径（含空格加引号、bash 命令串用正斜杠），其余裸 powershell.exe hook 超时时按同法逐个替换
- [生产删除前重查目标当前状态，归因须有直接证据](destructive-ops-recheck-live-state-and-require-direct-evidence.md) — 生产/共享环境的删除・停用・覆盖类写操作，执行前当场重查目标最新状态、归因要直接证据（旧快照 + 时间推断不作数）；误删用户在跑的导入 Session 的实害教训
