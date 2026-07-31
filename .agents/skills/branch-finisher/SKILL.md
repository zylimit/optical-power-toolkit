---
name: branch-finisher
description: 当用户要求收尾当前分支、合并前整理、处理 worktree/detached HEAD 或结束本轮开发时使用。
---

# Branch Finisher

## 目标

安全整理当前 Git/工作树状态、验证证据和合并选择，不隐式 commit、push、rebase、merge 或清理 worktree。

## 流程

1. 探测：repository root、status/branch、HEAD、upstream、ahead/behind、git dir/common dir、worktree 和 superproject。
2. 分类：正常分支、detached HEAD、managed/explicit worktree、dirty tree、冲突中、无 upstream。
3. 检查当前 task、fingerprint、quality/review receipt 和未验证项；Fast Mode 只放宽质量自动化，不放宽 Git/远端安全。
4. 给用户最多 3 个选择：
   - 继续修复未通过项
   - 准备本地提交/合并（展示精确文件、目标分支和新鲜验证）
   - 保留当前分支/worktree，记录恢复入口
5. 只有用户明确选择并授权后，才执行对应 Git 或远端动作；每一步后重新检查状态。

## 安全边界

- 不删除未合并分支或自己未创建/未确认的 worktree。
- dirty tree 不切分支、不 rebase、不覆盖文件。
- 不使用会批量丢弃或删除工作树内容的命令作为“清理”。
- push、force push、tag、PR/merge 和远端删除分别需要授权。
- 操作超时或中断后先核查本地/远端真实状态，不盲目重试。

## 回执

```text
Status: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED
Changed:
Verified:
Not verified:
Needs review by:
Evidence:
```
