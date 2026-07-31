---
name: release-builder
description: 当用户明确要求构建、打包、发布、部署或上线交付时使用。
---

# Release Builder

## 目标

在明确授权、可回滚和证据充分的前提下构建或发布。构建可本地执行；push、tag、publish、deploy、生产写和外部消息是独立远端副作用，必须逐项授权。

## 必需上下文

- 目标环境/渠道
- 精确版本或 artifact 标识
- 变更范围、commit 和 release notes
- 当前质量证据与已知风险
- 回滚方式
- 本次允许的每项远端操作

关键上下文缺失时返回 `NEEDS_CONTEXT`。不得从“发布一下”推断允许 force push、生产迁移、覆盖版本或外部通知。

## 流程

1. **读取实况**：分支/commit、dirty state、目标当前版本、部署健康和已有 artifact。
2. **影响与门禁**：运行 `affected`、`verify-plan` 和 release 适用的 build/security/smoke gate。Fast Mode 不豁免安全和远端授权。
3. **构建**：使用仓库已有命令；不擅自升级依赖、改签名配置或安装全局工具。
4. **产物审计**：核对版本、digest、manifest、SBOM/provenance（若项目要求）、runtime/evidence/session、密钥、个人路径、私密数据和 source map 策略。
5. **确认点**：展示目标、版本、命令、影响、回滚和将发生的远端副作用，等待用户明确批准。
6. **执行**：只执行批准动作。超时、断线、取消或 5xx 视为“可能已执行”，先查目标实况再决定是否重试。
7. **验收**：核对 artifact/commit、创建时间、健康检查和 live smoke；不能只信 deployer DONE。
8. **回滚**：失败时按批准方案回滚并重新验收，不掩盖部分成功。

陌生签名、公证、云平台或包管理器行为先查官方资料，或请求 researcher。

## 禁止

- 自动 push、tag、publish 或 deploy
- 输出或记录密钥
- 未授权生产迁移/删除
- 用旧测试结果给当前 artifact 背书
- 失败后盲目重复远端命令

## 回执

```text
Status: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED
Changed:
Verified:
Not verified:
Needs review by:
Evidence:
```

Evidence 至少包含 artifact 路径/digest、版本/commit、时间戳、质量门、目标健康和回滚入口。
