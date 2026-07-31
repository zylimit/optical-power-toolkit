---
name: dev-builder
description: 当 Product-Spec.md 与 DEV-PLAN.md 就绪，用户要求开始或继续实现某个 Phase/Task 时使用。
---

# Dev Builder

## 目标

按已确认的 Spec 和 DEV-PLAN 交付最小、可验证的代码切片。既有项目的架构、目录、语言规范、依赖和包管理约定优先；本 Skill 不强推框架、UI 库、测试框架、目录或文件行数。

## 前置

必须存在：

- `Product-Spec.md`
- `DEV-PLAN.md`
- 当前 Task 的 Goal / Scope / Out of Scope / Existing Pattern / Verification / Escalation

Design Brief、设计稿、ADR、module capsule、现有测试和 CI 为可选输入。缺失时说明降级，不虚构要求。涉及外部 SDK/API/版本或陌生错误时先查官方资料，或交回主 Agent 派 researcher。

## 流程

### 1. 恢复与圈定

- 查看 Git 状态、当前 diff 和 active task，保护已有用户改动。
- 读取相关 REQ、Plan Task、公共契约、调用方和测试。
- 大仓先运行：

```text
node .codex/runtime/harness.mjs catalog lint
node .codex/runtime/harness.mjs affected
```

unmapped、shared、global 或 truncated 结果必须安全扩大，不得忽略。

### 2. 建立任务

复杂、跨模块或中高风险任务先建立 task。用 JSON 传入完整 envelope、risk、ownedPaths、Spec/Plan 引用和 review exclusions：

```text
node .codex/runtime/harness.mjs task start --json <task.json>
node .codex/runtime/harness.mjs context pack
```

一个 Task 只交付一个可独立验收的行为切片。共享契约、schema、迁移、lockfile、生成物和公共 manifest 默认单 writer；并行写需独立 worktree、互斥 ownership 和 integration owner。

### 3. 实现

- 只修改 Scope/owned paths，遵循最近的现有模式。
- 保持公共接口稳定；必要破坏性变化先核对消费者和用户决策。
- 禁止空 catch、默认成功、静默重试和无真实调用方的兼容层。
- 行为变化处理相应错误、空状态、边界、权限、事务或并发路径。
- 文件拆分由职责、耦合、复杂度和可测试性决定，不按机械行数决定。
- 新增/升级依赖、迁移数据、切换包管理器或 CI 规则必须先获得授权。
- 未授权时不 commit、push、tag、publish、deploy、建远程仓库、终止进程或修改机器配置。

### 4. 受影响验证

先执行 Task 指定的最近检查，再查看唯一 verification plan：

```text
node .codex/runtime/harness.mjs verify-plan
node .codex/runtime/harness.mjs gate
node .codex/runtime/harness.mjs quality status
```

- `PASS` 才能称通过。
- `FAIL` 先修根因；`BLOCKED` 报告缺失条件；`SKIPPED` 报告 Fast 窗口和残余风险。
- 修改代码后 fingerprint/plan 变化，旧 receipt 不再背书。
- 高风险任务的完成门需要 fresh tester 执行 gate；实现者不能冒充 tester。

Fast Mode 下不自动新增测试或派 reviewer/tester，但用户显式要求的检查、安全门和不可跳过 check 仍执行。

### 5. 交回闭环

正常模式请求主 Agent：

- 派 fresh code-reviewer 对照当前 REQ/Task 和绑定 diff 审查。
- 按风险派独立 fresh tester 编写/运行高价值回归。
- 主 Agent 重跑关键命令并检查 receipt freshness。

Sub-Agent 不得派 Sub-Agent；需要其他角色时只写入 `Needs review by`。

## 完成标准

- Scope 内行为完成，Out of Scope 未混入。
- 错误和安全边界可观察，无未经授权副作用。
- 当前 fingerprint 的验证命令、退出状态和 evidence 可核查。
- 未验证、降级、环境限制和人工检查明确列出。

## 回执

```text
Status: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED
Changed:
Verified:
Not verified:
Needs review by:
Evidence:
```

不得用“应该没问题”“之前跑过”替代当前证据。
