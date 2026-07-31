---
name: test-builder
description: 当功能完成需要独立测试、补高价值回归或运行测试证据时使用。
---

# Test Builder

## 目标

以契约和风险为中心建立可重跑的回归防线，不追求覆盖率虚荣。测试作者必须独立于被测实现作者；主 Agent 派 fresh tester，本 Skill 不递归派发。

## 前置

- 被测行为、验收条件、Scope 和 exclusions 明确
- 当前 diff、impact 和代码路径可定位
- 已知 runner、fixture、测试目录和 CI 约定

新增测试框架、插件或 lockfile 变化必须取得授权；缺工具时返回 `BLOCKED` 或提出最小方案，不静默安装。

## 测试预算

优先级：

1. 跨边界契约、序列化往返、API/schema 兼容
2. 解析/清洗/去重/状态迁移
3. 权限、安全、路径、事务、并发和关键错误路径
4. 纯函数与高分支业务规则
5. 核心用户流程 E2E

通常不测第三方库自身、无分支透传和脆弱大快照，除非契约明确要求。

## 流程

1. 用 `affected`、task 和当前 diff 圈定直接模块、消费者和风险。
2. 从 Spec/issue/契约提取独立断言，不照抄实现逻辑。
3. 列出候选用例、破坏代价和取舍理由。
4. 只修改测试、fixture 和授权的测试配置；不得修改生产代码。
5. 先跑最近测试，再按 plan 执行 gate。高风险 gate 使用：

```text
node .codex/runtime/harness.mjs gate --executor-role tester --executor-id <fresh-tester-id>
```

6. 将失败分类为 product / test / environment / prerequisite / suspected-flaky。业务缺陷交回主 Agent 路由 bug-fixer；测试缺陷由 tester 修正。
7. 修改后重跑，只接受当前 fingerprint 的 fresh receipt。

Fast Mode 下不自动新增或运行测试；用户显式要求测试时照常执行。

## 完成标准

- 高风险相关必测项可重跑且真实执行。
- 运行器输出 passed/failed/exit status 可核查。
- 无遗留红测；无法执行项明确为 BLOCKED 或 Not verified。
- 列出每个新增用例防止的回归，以及未测项和理由。
- 不自动 commit/push。

## 回执

```text
Status: PASS | FAIL | NEEDS_CONTEXT | BLOCKED
Changed:
Verified:
Not verified:
Needs review by:
Evidence:
```
