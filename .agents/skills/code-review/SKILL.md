---
name: code-review
description: 当用户要求审查代码、对抗审查、检查质量或对照规格核查实现时使用。
---

# Code Review

## 目标

只读、独立、缺陷优先地判断变更是否满足 Spec/Task，是否引入 correctness、安全、兼容、维护和发布风险。审查者不修改代码；修复交回主 Agent。

## 输入

- REQ/Task/ADR/设计或其他明确行为契约
- full base commit 与 canonical diff hash
- Scope、exclusions、Out of Scope
- 已执行验证及 evidence

既有项目没有固定 Spec 时，可使用 issue、验收条件、公共契约和现有测试。base/diff/scope/exclusions 不完整时，报告为未绑定审查，不能 APPROVE。

大型仓库先运行 `affected`，只读加载模块 capsule、公共契约、消费者和相关测试；不要倾倒全仓。

## 三阶段

### Stage 0：客观证据

- 检查 receipt 是否绑定当前 fingerprint/plan/diff。
- 读取 `verify-plan` 和实际 gate 结果；结构 validate 不能代替质量 PASS。
- `FAIL`、`BLOCKED`、`SKIPPED` 和未运行项如实报告。

### Stage 1：规格与行为

逐条检查范围内契约：

- happy/error/empty/loading/boundary
- 状态、事务、并发、重试和幂等
- API/schema/序列化/迁移兼容
- 权限、路径、输入校验和数据隔离
- 漏实现、半实现、scope creep 和旧行为回归

每个 finding 必须包含严重度、`path:line`、触发路径或推理链、具体影响和最小修复方向。

### Stage 2：代码与运维

- 模块职责、耦合、重复、局部复杂度、可测试性
- 错误是否可观察，是否存在空 catch/默认成功
- 依赖和配置变化是否必要且受控
- 测试是否覆盖高价值契约，而不是覆盖率表演
- 安装/升级/卸载、package hygiene、Windows/POSIX、编码、回滚、隐私和远端副作用

不要用机械行数、个人命名偏好或固定框架规则制造噪声；以仓库现有规范为准。

## 对抗模式

高风险变更由主 Agent组织 Blue/Red/Judge：

- Blue 提供绑定 diff、实现证据和验证结果。
- fresh Red 主动构造失败输入、竞态、异常和绕过路径。
- 主 Agent Judge 只按证据裁定，不按 Agent 数量投票。

多个只读 reviewer 可并行；任何修复都会改变 diff，必须重新生成 review receipt。

## 输出

Findings 必须排在最前，按 `Critical > High > Medium > Low`：

```text
[Severity] Title
Location: path:line
Evidence: reproduction or reasoning
Impact: concrete failure
Fix: minimum direction
```

随后列出 Open questions、Verified、Not verified、残余风险和 base/diff hash。无 finding 时明确写“未发现 finding”，并说明实际攻击过的路径和测试缺口。
