---
name: skill-builder
description: 当用户要求创建新 Skill，或 EVOLUTION.md 提议新增框架技能时使用。
---

[任务]
    根据用户描述的需求或 EVOLUTION.md 的第四层提议，创建符合框架规范的新 Skill。
    确保新 Skill 和现有 Skill 结构一致、风格统一、可像积木一样即插即用。

[依赖检测]
    必需：无（本 Skill 不依赖外部文件）

    可选：
    - .agents/feedback/ 中的相关记录 → 如来自 EVOLUTION.md 提议，读取原始 feedback 了解需求背景

[第一性原则]
    **模板优先**：先读 templates/skill-template.md 骨架，按结构填充。不从零开始写。

    **参照现有**：创建前先读 1-2 个已有 Skill 作为参考，保持风格一致。不发明新的格式。

    **行为红绿先行**：Skill 是行为补丁，不是愿望清单。创建或大改 Skill 前必须先写 2-3 个会诱发错误行为的压力场景；能跑行为测试时先记录 baseline 失败，再用 Skill 内容堵住失败路径，最后复测通过。

    **可自动化优先**：能用 doctor/test/hook/regex 静态检查拦住的规则，不只写进 Skill；文档负责判断，脚本负责机械约束。

    **最小必要**：只创建需要的 Section。不为了"看起来完整"而加空内容或无关规则。

    **联网优先**：如果新 Skill 涉及不熟悉的领域，先 WebSearch 了解该领域的最佳实践和常见问题，再设计维度清单和策略。
    **来源可追溯**：从外部仓库、Skill、Prompt 或模板吸收内容时，记录来源 URL/路径、ref、访问日期、许可证和实际读过的文件；没有打开的来源不引用。
    **复制处理显式**：外部内容逐项标为 `vendored_intact`、`vendored_modified`、`synthesized`、`referenced_only` 或 `excluded`。许可证未知/不兼容、包含密钥/会话/运行态/隐藏安装行为的内容默认不复制。
    **源码与运行态分离**：Skill 源码只放稳定、可复核、可版本化的能力；项目任务、进度、日志、缓存、凭证、Provider 状态和生成投影不得写进 Skill 目录。

[文件结构]
    ```
    skill-builder/
    ├── SKILL.md                           # 主 Skill 定义（本文件）
    └── templates/
        └── skill-template.md              # 新 Skill 的骨架模板
    ```

[创建规范]
    [三层模块化]
        框架的三层架构，每层独立、互不耦合：

        **第一层：原子能力（Section）**
        每个 Skill 由多个独立的 Section 组成，每个 Section 是一个原子能力模块：
        - [维度清单] — 定义"检查什么 / 收集什么"
        - [策略] — 定义"怎么做"
        - [工作流程] — 定义"什么顺序做"
        - [依赖检测] — 定义"需要什么前置条件"
        这些是积木块——可以在不同 Skill 中复用相同模式。
        改一个 Section 不影响其他 Section。

        **第二层：Skill（SKILL.md）**
        一个 Skill = 多个原子能力的组合，解决一个完整的领域问题。
        改一个 Skill 不影响其他 Skill。

        **第三层：工作流（AGENTS.md）**
        AGENTS.md 编排多个 Skill 的执行顺序和触发条件。
        改工作流不需要改 Skill 内容。

    [Section 分类]
        **必须有**（所有 Skill 都有）：
        - [任务] — 一句话说清楚做什么
        - [依赖检测] — 启动时检查前置条件
        - [第一性原则] — 3-5 条核心原则
        - [文件结构] — Skill 目录结构
        - [初始化] — 入口点

        **推荐有**（大多数 Skill 有）：
        - [输出风格] — 语态 + 原则 + 典型表达
        - [XXX维度/规则清单] — 领域特定的检查维度（名称根据领域定制）
        - [XXX策略] — 领域特定的方法论（名称根据领域定制）

        **按需有**（特定类型的 Skill 需要）：
        - [信息充足度判断] — 收集 / 分析型 Skill
        - [回退策略] — 发布 / 部署类 Skill
        - [Phase 完成度判断] — 开发类 Skill
        - 多模式工作流程 — 有多种执行模式的 Skill

    [命名规范]
        - Skill 名：kebab-case（如 skill-builder、dev-planner）
        - 目录：.agents/skills/[skill-name]/
        - 主文件：SKILL.md
        - 模板文件（如有）：templates/ 子目录

    [格式规范]
        - Section 标题用 [标题] 格式
        - 内容四空格缩进
        - frontmatter 只有 name 和 description
        - 中文编写

[工作流程]
    [第一步：需求收集]
        了解用户想要什么新 Skill：
        - 这个 Skill 解决什么问题？
        - 什么时候触发？（自动触发的条件 / 手动调用）
        - 输入是什么？（前置文件、用户输入、项目状态）
        - 产出是什么？（文件、报告、代码变更）
        - 如果来自 EVOLUTION.md 第四层提议 → 读取 feedback/ 中的原始记录，了解需求背景

    [第二步：参照现有]
        按交互模式（不是领域）找 1-2 个最接近的已有 Skill 作为参照：
        - **对话采集型**（需要和用户多轮对话收集信息）→ 参照 product-spec-builder、design-brief-builder
        - **自主分析型**（读取输入自主分析输出结果）→ 参照 dev-planner、code-review
        - **执行操作型**（直接执行具体操作产出成果）→ 参照 dev-builder、release-builder
        - **诊断修复型**（先诊断问题再执行修复）→ 参照 bug-fixer
        新 Skill 可能是任何领域——不一定是软件开发，可能是内容写作、数据分析、竞品调研等。
        按交互模式匹配参照，不按领域匹配。
        了解参照 Skill 的结构、维度命名、策略风格、输出格式

        如果参照来自外部来源，先建立最小来源清单：
        - 来源定位、ref、访问日期、许可证
        - 盘点 skills、references、scripts、templates、assets、hooks、plugin manifests、licenses，以及任何写状态或隐藏安装行为
        - 计划吸收的路径与实际读过的证据
        - 每项 copy treatment 与修改说明
        - 明确排除的运行态、私密、宿主专用和无关内容
        未完成来源清单前，不直接复制外部内容。

        多候选或高风险来源才启用 hard gate：许可证、密钥/运行态、维护状态、宿主适配、可验证性和形态适配。单一清晰来源不为形式制作重型评分表。

    [第三步：确定结构]
        读取 templates/skill-template.md 骨架
        确定需要哪些 Section：
        - 必须有的 5 个 → 全部保留
        - 推荐有的 → 根据领域判断是否需要
        - 按需有的 → 根据 Skill 类型判断
        确定领域特定的名称：[XXX维度清单] 和 [XXX策略] 的 XXX 叫什么

        如涉及外部来源或复杂能力，写入前形成最小 blueprint：
        - Skill 名称、触发条件与非目标
        - `single-skill` / `multiple-skills` / `workflow-or-agent-role` 形态判断
        - source → target 内容映射、copy treatment 与排除项
        - write scope、do-not-touch 与 stop conditions

    [第三点五步：设计行为红绿场景]
        在写正文前列出 2-3 个压力场景：
        - 触发语句：用户会怎么说
        - 易错行为：没有这个 Skill 时 Agent 可能怎么偷懒、误判或越界
        - 期望行为：Skill 必须强制 Agent 怎么做
        压力场景必须覆盖最危险的失败模式，不写泛泛的 happy path。

        如果当前环境可运行 Codex/子 Agent 行为测试：
        - RED：先用触发语句跑 baseline，记录未加载/误加载 Skill、跳步骤、提前动手、无证据声称完成等失败现象
        - GREEN：写完 Skill 后复跑同一触发语句，确认行为转为期望路径
        - REGRESSION：把可自动化的触发语句加入本 Skill 的 `scripts/test-skill-behavior.sh` 或同类行为测试；暂时不能自动化的，写入 Skill 自检清单并说明原因

        如果当前环境不能运行行为测试，也必须保留压力场景表，不允许只写 happy path 示例。

    [第四步：填充内容]
        逐个 Section 填写：
        - [任务] — 一句话，如有多种模式分别说明
        - [依赖检测] — 列出必需和可选依赖
        - [第一性原则] — 3-5 条，最后一条是联网优先
        - [维度清单] — 这个领域需要关注什么？分必须 / 推荐 / 可选
        - [策略] — 这个领域怎么做？用什么方法论？
        - [工作流程] — 按什么顺序做？引用维度清单和策略
        如涉及不熟悉的领域 → WebSearch 了解最佳实践

    [第五步：创建文件]
        在 .agents/skills/[skill-name]/ 下创建 SKILL.md
        如有模板文件 → 创建 templates/ 子目录
        写完后自检：
        - 所有必须 Section 都有？
        - 格式一致（[标题] + 四空格缩进）？
        - frontmatter 只有 name 和 description？
        - description 是否只写触发条件、不写流程摘要？（跑 `bash .agents/skills/skill-builder/scripts/skill-description-lint.sh`）
        - 风格和参照的现有 Skill 一致？
        - 压力场景中的易错行为是否都有明确规则拦截？
        - 能行为回归的场景是否已加入 `scripts/test-skill-behavior.sh`，或明确说明为何暂不自动化？
        - 能自动化检查的规则是否已加入 doctor/test/hook 或说明为什么暂不自动化？
        - 终态是否明确为 `complete` / `draft` / `rollback` / `blocked`？`draft` 或 `blocked` 是否列出 dirty paths、未验证项和继续条件？

    [第六步：注册到 AGENTS.md]
        Codex 会自动发现 .agents/skills/ 下的新 Skill。
        但需要在 AGENTS.md 中补充：
        1. [Skill 调用规则] — 加新 Skill 的触发条件（自动 / 手动）
        2. [可用技能] — 加一行 `/[skill-name] - [描述]`
        3. [工作流程] — 如新 Skill 需要在主流程中有对应阶段，补充阶段定义

[初始化]
    执行 [第一步：需求收集]
