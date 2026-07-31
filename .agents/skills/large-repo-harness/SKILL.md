---
name: large-repo-harness
description: 当仓库规模大、跨模块、上下文易失控，或需要配置 catalog、impact、context pack 与 affected verification 时使用。
---

# Large Repo Harness

## 目标

让 20-30 万行、多模块、长周期仓库通过显式模块边界、预算化上下文、串行 ownership 和 affected-first 验证保持可控。扩展方式是缩小活动范围，不是把更多源码灌入模型。

## 何时使用

- 新接入大型既有仓库
- 一个变更跨 2 个以上模块/公共契约
- catalog 出现 unmapped/overlap/shared/global/truncated
- 全仓测试成本过高或上下文频繁压缩
- 多 writer、worktree、生成物、迁移或公共 manifest 需要协调

## 1. Catalog Onboarding

先运行：

```text
node .codex/runtime/harness.mjs catalog discover
node .codex/runtime/harness.mjs catalog lint
```

在 `.codex/harness/module-catalog.json` 为每个 bounded module 定义：

- `id`、`root`、`paths`
- `dependsOn`、`shared`
- `owners`
- `contracts`、`capsule`、`tests`
- `verification` check IDs

所有 tracked path 必须进入 fine module、global 或 ignored-with-reason。不要用 root catch-all 掩盖漏项；generated/vendor/runtime/secret 需显式排除。

## 2. Task Slicing

每个 Task 只包含一个可独立验收的行为切片：

- 明确 owned paths、公共契约和 consumers
- schema、迁移、lockfile、生成物、根配置视为共享 ownership
- 共享 checkout 默认一个 writer
- 并行写只允许独立 worktree + 不交叉 ownership + integration owner

复杂任务用 `task start --json` 记录 baseline、risk、Spec/Plan refs 和 exclusions。

## 3. Impact

```text
node .codex/runtime/harness.mjs affected
node .codex/runtime/harness.mjs affected --baseline <commit>
```

- 直接模块沿 reverse dependencies 扩到消费者。
- shared/global/unmapped/overlap/truncated 安全扩到全部模块。
- 删除文件、unborn/non-Git 和无效 baseline 必须明确 degrade/block，不伪造精确影响。

## 4. Context Pack

```text
node .codex/runtime/harness.mjs context pack
```

Pack 应包含 task envelope、Spec/Plan 指针、current diff、changed files、capsule、contracts、依赖/消费者契约和测试入口；严格执行单文件、总字符、文件数和 diff 预算。秘密、`.git`、依赖目录、构建产物和 harness runtime 永不进入。

缺 contract/capsule 时记录 omission，不用全目录源码补洞。长期缺失应回到 catalog/文档维护，而不是每轮重新猜。

## 5. Verification Budget

```text
node .codex/runtime/harness.mjs verify-plan
node .codex/runtime/harness.mjs gate
```

顺序：changed-file static -> module unit -> consumer contract/integration -> broader build/security/smoke。只有跨切面、高风险、release/CI 或 conservative impact 才默认全仓。

同一 check 的后续 FAIL/BLOCKED 覆盖旧 PASS。高风险任务由 fresh tester 使用 `--executor-role tester` 执行 plan；结构 validate 不生成质量 receipt。

## 6. 压力场景

- **unmapped path**：停止声称精确影响，修 catalog 或执行 conservative checks。
- **shared contract**：扩到全部消费者，串行修改公共接口。
- **多 writer**：发现 ownership 重叠立即停止；不要靠 prompt 假装隔离。
- **全仓测试昂贵**：优化 matrix/capsule/contract tests，不直接关闭 required gate。
- **context 截断**：拆 Task、补 capsule、降低噪音；不要提高预算掩盖边界失败。

## 回执

```text
Status: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED
Changed:
Verified:
Not verified:
Needs review by:
Evidence:
```
