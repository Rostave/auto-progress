# Unity 自动维护

该上下文描述 Codex 对 Unity 仓库进行周期性、小步、人工审查式维护时使用的统一语言。

## Language

**受管项目（Managed Project）**:
已通过项目策略选择源码控制与审查托管适配器、启用周期性 Codex 维护流程的 Unity 项目。
_Avoid_: 自动提交项目、刷绿项目

**AutoProgress 插件（AutoProgress Plugin）**:
集中提供每日维护、项目配置和人工指令录入能力、可供受管项目安装使用的 Codex 插件。
_Avoid_: 单一 skill、仓库脚本

**源码控制适配器（Source Control Adapter）**:
为一种源码控制工具提供项目身份、版本快照、工作区转换和变更交付语义的 AutoProgress 路由实现；当前只有 Git 适配器。
_Avoid_: 托管平台、GitHub 适配器、通用仓库脚本

**审查托管适配器（Review Host Adapter）**:
为一种代码审查托管平台提供身份验证、变更请求、审查和检查状态语义的 AutoProgress 路由实现；当前只有 GitHub 适配器。
_Avoid_: 源码控制、Git 适配器、PR 脚本

**基准快照（Base Snapshot）**:
源码控制适配器为一次运行冻结、作为变更来源和项目策略来源的不可变版本状态；Git 适配器以 commit SHA 表达它。
_Avoid_: 最新代码、本地分支、工作区 HEAD

**变更上下文（Change Context）**:
源码控制适配器为一次运行建立、用于隔离和承载候选改动的工具特定上下文；Git 适配器以工作分支或 worktree 表达它。
_Avoid_: 工作分支、临时目录、模型工作区

**适配器能力（Adapter Capability）**:
适配器对自身可提供的安全生命周期语义所作的机器可判定声明，用于在任务开始前与任务类型的必需能力严格匹配。
_Avoid_: 工具特性、模型降级、可选步骤

**适配器注册表（Adapter Registry）**:
由受信任 AutoProgress 插件包提供、将稳定适配器 ID 映射到兼容实现与能力声明的本地权威目录。
_Avoid_: 项目脚本列表、命令路径、模型工具选择

**适配器状态迁移（Adapter State Migration）**:
由适配器提供、把未终结运行的旧状态 schema 确定性转换为当前兼容状态且保留原状态证据的恢复步骤。
_Avoid_: 模型解释旧状态、项目配置迁移、重新运行

**Unity 验证适配器（Unity Validation Adapter）**:
由确定性入口直接调用、通过已配置 MCP endpoint 验证 Unity 项目身份、Editor 状态和编译结果的可选工具实现。
_Avoid_: 模型 MCP 调用、Unity 进程检测、C# 命令验证

**确定性执行层（Deterministic Execution Layer）**:
AutoProgress 中由普通程序依据显式输入完成严格校验、状态转换和机械操作，并返回结构化成功结果或明确失败原因的职责边界。
_Avoid_: 辅助脚本、模型工具调用、提示词检查

**模型推理层（Model Reasoning Layer）**:
AutoProgress 中由大模型承担语义理解、方案设计、代码编写与审查，并把确定性执行结果组织成人类可读交互的职责边界。
_Avoid_: 工作流引擎、条件校验器、脚本包装器

**确定性豁免规则（Deterministic Exemption Rule）**:
由受版本控制的项目策略明确声明、供确定性执行层判定某类已知无害状态不构成失败的有限规则；它不授予模型覆盖脚本结论的权力。
_Avoid_: 模型放行、临时忽略、软失败

**额外忽略规则（Additional Ignore Rule）**:
项目策略在 Git 自身忽略规则之外，对已知无害未跟踪路径声明的附加 ignore 规则；它不改变仓库的 `.gitignore`，也不适用于任何已跟踪或 staged 状态。
_Avoid_: AutoProgress gitignore、额外 .gitignore、工作区通用豁免

**工作区准入（Workspace Admission）**:
AutoProgress 在接管原工作区和进入目标运行状态时分别证明人工现场受到保护、运行环境满足安全门槛的两阶段资格。
_Avoid_: 单次 preflight、checkout 检查、工作区清理

**工作区恢复义务（Workspace Restoration Obligation）**:
确定性执行层在未开始核心工作前发生状态转换失败时，将所有可逆工作区状态恢复到已记录起点的责任；无法安全恢复时必须保留现场并阻止后续运行。
_Avoid_: 自动清理、尽力回滚、失败后继续

**确定性阶段（Deterministic Stage）**:
具有显式前提、单一职责和明确完成边界的一次确定性执行；成功完成后形成后续阶段不得撤销的事实。
_Avoid_: 大脚本、提示词步骤、整次运行

**确定性入口（Deterministic Entry Point）**:
供模型粗粒度调用、在代码内部编排一个或多个确定性阶段及其检查点、重试和恢复协议的命令边界。
_Avoid_: 单命令包装器、模型工作清单、确定性阶段

**交付清单（Delivery Manifest）**:
模型为一次候选交付声明改进项归属、用户可读摘要、验收说明和设计取舍的有界语义输入；实际变更、验证和版本控制事实不以清单声明为准。
_Avoid_: PR 正文、Git diff、运行状态

**已验证内容指纹（Validated Content Fingerprint）**:
对一次验证实际覆盖的模型改动路径、内容身份和版本属性所作的稳定摘要，用于证明验证后除指定确定性产物外没有内容变化。
_Avoid_: 整仓库 hash、commit SHA、构建缓存键

**阶段检查点（Stage Checkpoint）**:
确定性阶段成功完成后保存、允许后续阶段查询实际结果并安全继续的持久事实。
_Avoid_: 临时输出、模型记忆、回滚点

**静默成功（Silent Success）**:
确定性阶段成功且无需人工行动时，仅作为后续推理和阶段执行依据而不主动打扰用户的交互结果。
_Avoid_: 隐藏失败、无日志运行、成功通知

**确定性重试（Deterministic Retry）**:
由代码依据稳定失败分类、对账结果和固定上限执行的安全阶段重试，不依赖模型猜测瞬时故障或副作用状态。
_Avoid_: 模型重跑、无限重试、失败放行

**AutoProgress 任务类型（AutoProgress Task Type）**:
占用每日活动额度、具有独立资格检查、核心工作和完成规则的可扩展任务类别；当前包含处理批次改进与寻找改进项。
_Avoid_: 改进项类型、运行结果、skill 名称

**项目策略（Project Policy）**:
受管项目中经人工确认、随仓库版本控制的自动维护授权边界。
_Avoid_: 自动化配置、Codex 设置

**基准分支（Base Branch）**:
人工指定、工作分支创建时所依据且自动 PR 唯一允许合入的分支。
_Avoid_: 原分支、默认分支

**工作分支（Work Branch）**:
一次运行从基准分支创建、用于承载候选改动的临时分支。
_Avoid_: 自动提交分支、Codex 分支、目标分支

**维护批次（Maintenance Batch）**:
一次维护运行中共同使用环境准备、工作分支、最终验证和自动 PR 的一组兼容改进项；每个改进项仍保有独立 ID、验收条件和 commit，批次不是新的改进项。
_Avoid_: 大改进项、杂项 PR、批量任务

**批次兼容性（Batch Compatibility）**:
多个改进项可以在同一维护批次安全实施和审查的条件；它们应当位于相同或相邻模块、没有范围或行为冲突，并能共享验证路径且不突破批次预算。
_Avoid_: 都很小、凑满数量、任意组合

**批次就绪（Batch Ready）**:
普通自动改进项已经在发现阶段记录足够的模块、预计路径、验证方式和兼容性信息，使维护运行能够直接组批而无需重新扫描代码寻找候选项的状态。
_Avoid_: 已实现、无需复核、保证可合并

**候选项新鲜度（Candidate Freshness）**:
改进项的证据路径和预计修改路径相对发现时基准仍足以支持原判断的状态；维护运行只对这些已记录路径做有界复核，不以新鲜度检查为名重新发现候选项。
_Avoid_: 最新代码、重新审查、自动刷新

**批次部分交付（Partial Batch Delivery）**:
维护批次中部分改进项未能安全完成时，只交付通过验证的成功子集，并完整记录未交付项及原因的结果；失败项保持独立身份，不由临时候选项替补。
_Avoid_: 部分实现、忽略失败、拆分 PR

**改进项（Improvement）**:
具有明确价值、有限范围和可验证结果的小型项目改进。
_Avoid_: 点子、任务、优化点

**人工指令改进项（Directed Improvement）**:
由人工明确加入改进池、用于强行干预项目进度并优先于自动发现候选项进行选择的改进项；它显式豁免拒绝清单，但不因此自动豁免其他执行安全边界。
_Avoid_: 强制任务、紧急点子

**指令录入入口（Directive Authoring Entry）**:
只能由人工显式调用、用于创建和校验人工指令改进项的独立入口；它不实现改进项本身。
_Avoid_: 自动指令生成器、每日任务入口

**暂停调度（Schedule Pause）**:
停止当前尚未启动及后续维护日的自动许可请求与运行，不终止已经开始的运行，也不删除项目配置或历史。
_Avoid_: 删除任务、取消配置、终止运行

**工作区租约（Workspace Lease）**:
人工授予一次运行临时独占原始 Unity 项目目录的许可；租约期间其他人工或自动工作流不得使用该目录。
_Avoid_: 文件锁、worktree、后台权限

**运行账本（Run Ledger）**:
保存在 Codex 本地状态目录、以追加事件方式记录许可、运行和结果的统计事实来源。
_Avoid_: Git 日志、状态文档

**运行状态（Run State）**:
保存在 Codex 本地状态目录、记录一次尚未终结运行的阶段检查点、当前事实和恢复义务的代码所有状态。
_Avoid_: 模型记忆、运行账本、仓库状态文件

**运行记录（Run Record）**:
由代码根据交付清单与确定性事实生成、随实际改动 review 提交的人类可读文档，描述一次运行的选择依据、验证、豁免和产物。
_Avoid_: 运行账本、日报

**状态报告（Status Report）**:
由人工从运行账本按指定时间范围导出的可再生 Markdown 统计快照。
_Avoid_: 运行账本、实时状态

**改进项 ID（Improvement ID）**:
在受管项目内唯一且长期稳定、用于引用一个改进项的标识。
_Avoid_: 点子编号、任务号

**运行 ID（Run ID）**:
在受管项目内唯一、用于关联任一 AutoProgress 任务类型的一次任务实例、额度占用及其安全重试事件的通用标识；任务类型作为独立字段记录，跨日恢复使用新的运行 ID。
_Avoid_: 改进项 ID、commit ID

**改进池（Improvement Backlog）**:
随仓库版本控制、记录尚未实现的改进项及其判断依据的集合。
_Avoid_: 点子文档、TODO 列表

**改进发现会话（Improvement Discovery Session）**:
由人工显式触发、独立于实现维护，使用审查切片发现、评估和去重自动候选项的过程；它不实现改进项、不占用自动实现额度，并独占租用原始 Unity 项目目录。
_Avoid_: 预发现任务、每日发现、实现维护

**改进发现 PR（Improvement Discovery PR）**:
由一次改进发现会话创建、只承载候选改进项文档的独立 Draft PR；候选项只有经人工审查并随该 PR 合入基准分支后，才成为权威改进池中的排队项。
_Avoid_: 点子 PR、维护 PR、自动入池

**发现冷却期（Discovery Cooldown）**:
发现 PR 被关闭但未合并后，阻止其中候选项被立即重复提出的一段维护日区间；关闭不等于拒绝，冷却结束后只有证据或方案发生实质更新才能沿用原改进项 ID 再次提出。
_Avoid_: 拒绝期、永久排除、新点子

**每日活动额度（Daily Activity Allowance）**:
一个维护日内只允许一次定时 `implement-batch` 启动核心工作的额度；人工触发的实现、发现和管理任务均不读取或占用它。
_Avoid_: 维护完成、运行许可、Token 配额

**触发来源（Trigger Source）**:
明确标识一次运行由人工直接调用还是由定时任务触发的必填属性；它决定实现运行是否竞争每日活动额度，但不改变其他安全门禁。
_Avoid_: 提前运行、任务类型、调用者

**改进项状态（Improvement State）**:
写入改进项文件名和 frontmatter 的持久生命周期状态，仅包含 `queued`、`implemented` 和 `cancelled`；`implemented` 表示改进已通过最终验证并形成独立实现 commit，不要求 push、PR 或合并成功。
_Avoid_: 单次结果、PR 合并状态、运行状态

**旁路人工改动（Bypassed Human Change）**:
在实现交付入口开始时已存在、与全部目标路径不重叠并可安全带回原分支的人工工作区改动；它以路径和内容指纹冻结，排除在实现 commit 与 PR 之外。
_Avoid_: 忽略文件、允许修改、自动清理

**仓库规范文档（Repository Guidance Document）**:
由项目配置逐项声明、约束改进代码实现与验证的仓库内说明文件；默认位置包括 `AGENTS.md`、`CLAUDE.md` 和 `.github/copilot-instructions.md`。
_Avoid_: AutoProgress 策略、系统提示词、发现资料

**仓库规范快照（Repository Guidance Snapshot）**:
按单个规范文档 Git blob SHA 缓存在仓库外的内容快照；仅对应文档 SHA 变化时重新读取，多个文档不得合并为一个缓存实体。
_Avoid_: 配置锁定、规则副本、运行日志

**插件发布版本（Plugin Release Version）**:
标识一个可安装 AutoProgress 发布及其 Codex 缓存产物的 SemVer 2.0 版本，与受管项目的策略 schema 版本相互独立。
_Avoid_: 配置版本、schema 版本、缓存版本

**目标库存（Backlog Target）**:
改进池希望保有的普通自动 `queued` 改进项数量，用于决定改进发现会话是否需要补充候选项；人工指令项、编译修复和未合并候选项不计入，它是停止发现的软目标而不是必须凑满的配额。
_Avoid_: 最低任务数、每日指标、填满要求

**发现关注范围（Discovery Focus）**:
人工在触发改进发现会话时可选指定的允许路径，用于替代审查游标决定本次审查切片；它可以越过模块轮转冷却，但不放宽审查预算、排除规则或候选项准入门槛。
_Avoid_: 全项目扫描、强制改进范围、规则豁免

**审查切片（Review Slice）**:
一次改进发现会话在时间、文件数量与源码行数预算内实际检查的近期变化和轮转模块集合；它可以在候选项不足时分轮扩展，但始终受硬上限约束。
_Avoid_: 全量扫描、随机抽查

**审查游标（Review Cursor）**:
保存在运行账本、用于避免反复检查同一模块并逐步覆盖允许范围的位置状态。
_Avoid_: Git 游标、改进池状态

**改动预算（Change Budget）**:
项目策略为单个改进项规定的建议规模与绝对最大规模，不包含任何必须达到的最低改动量。
_Avoid_: 改动目标、代码量指标

**拒绝清单（Rejection Register）**:
随仓库版本控制、由具体 `IMP-ID` 拒绝记录目录与预防性拒绝规则文档共同组成的权威集合。
_Avoid_: 拒绝文档、黑名单

**改进项拒绝记录（Improvement Rejection Record）**:
以既有 `IMP-ID` 命名、记录人工拒绝理由、排除模式和适用范围的独立文档；拒绝决定与理由必须来自人工。
_Avoid_: 自动拒绝、关闭 PR、取消状态

**预防性拒绝规则（Proactive Rejection Rule）**:
在尚无对应改进项时由人工写入单一规则文档、使用稳定 `REJ-<kebab-case>` 标识的排除规则。
_Avoid_: 虚构 IMP、候选项、拒绝状态

**排除规则（Exclusion Rule）**:
拒绝清单中对被拒绝方案的共同特征和适用范围所作的明确描述，用于阻止不同 ID 但实质相似的改进项再次进入改进池。
_Avoid_: 相似点子、同类方案

**例外申请（Exception Request）**:
Codex 针对拒绝清单中的指定记录，向人工提出拓宽、修改或一次性放行请求的说明；它本身不授予修改或实施权限。
_Avoid_: 自动放行、规则修复

**AutoProgress PR**:
由 AutoProgress 任务创建、等待人工审查且不能自动合并的 PR 总称；当前分为实现 PR 与改进发现 PR。
_Avoid_: 自动提交、每日 PR、自动 PR（未区分类型时）

**实现 PR（Implementation PR）**:
由 `implement-batch` 任务创建、承载一个维护批次及其改进项文档状态变更的 Draft PR。
_Avoid_: 单项 PR、改进发现 PR

**跳过（Skip）**:
一次运行在没有安全且有价值的改进项，或未满足执行门槛时，不产生 commit、push 或 PR 的正常结果。
_Avoid_: 失败、空跑

**运行许可（Run Approval）**:
每日启动窗口内由人工授予、允许启动当次自动维护运行的一次性许可。
_Avoid_: 自动确认、启动提醒

**启动窗口（Launch Window）**:
一个维护日内允许请求许可并启动自动维护运行的时间范围，不限制已经获批运行的完成时间。
_Avoid_: 执行窗口、运行时间

**维护日（Maintenance Day）**:
按项目策略时区划分、用于归属和去重自动维护运行的日历日。
_Avoid_: 提交日、UTC 日期

**提前运行（Early Run）**:
在当个维护日的启动窗口开始前，由人工主动触发的自动维护运行。
_Avoid_: 临时运行、手动任务

**待恢复工作（Pending Recovery）**:
已经在工作分支产生 commit 或 push、但尚未成功创建自动 PR 的跨日工作。
_Avoid_: 失败 PR、残留分支

**恢复运行（Recovery Run）**:
只验证、push 并尝试为待恢复工作创建 PR，不选择或实现新改进项的运行。
_Avoid_: 重跑、补跑
