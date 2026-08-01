# AutoProgress

AutoProgress 是一个面向 Unity C# 项目的 Codex 插件。它帮助你发现值得做的代码改进、维护改进队列，并以可审查的 Draft Pull Request 交付实现结果。

它不会自动合并 PR，也不会替你解决 Git 冲突。你始终拥有最终审查与合并权。

## 适合什么场景

- 定期检查 Unity C# 代码，补充可执行的改进候选。
- 按优先级实现已确认的改进项，一次最多组成一个小批次。
- 临时加入人工指定的改进任务。
- 记录不希望再次提出的方案，形成项目级拒绝策略。
- 在任务中断后继续已有分支、提交、推送或 PR 交付。
- 复用已打开的 Unity Editor，通过 Unity MCP 刷新并检查编译结果。

## 0.3.0 新功能

相比 0.2.0，0.3.0 引入以下面向使用者的变化：

- **人工运行不占每日额度**：手动执行发现、实现或管理任务不限制次数；只有定时触发的实现任务占用每日额度。
- **仓库规范感知**：可分别读取 `AGENTS.md`、`CLAUDE.md` 和 `.github/copilot-instructions.md`，仅在对应文档发生变化时刷新缓存。
- **两级拒绝机制**：既可按具体 `IMP-ID` 记录拒绝，也可提前声明不希望再次出现的方案模式。
- **新增拒绝入口**：使用 `$record-improvement-rejection` 为指定改进项创建拒绝记录 Draft PR。
- **发现任务更轻量**：复用仓库外的常驻 worktree，不复制 Unity `Library`，结束后自动停放。
- **改进项状态更直观**：新文件使用 `--queued.md`、`--implemented.md` 或 `--cancelled.md` 后缀。
- **配置升级到 schema v4**：新增仓库规范文档和预防性拒绝规则配置；旧配置需要显式迁移。
- **标准化发布**：插件版本使用 SemVer，发布标签使用 `vMAJOR.MINOR.PATCH`。

## 安装

### 环境要求

- 支持本地 marketplace 的 Codex。
- Python 3.11 或更高版本。
- Git，并已在目标项目中配置身份和远端。
- GitHub CLI（`gh`），并已登录可访问目标仓库的账号。
- Unity C# 项目，以及一条可从命令行执行的真实构建命令。
- Unity MCP 可选；不使用时仍可通过普通 C# 构建完成验证。

### 从仓库安装

克隆仓库后，将其中的 marketplace 加入 Codex：

```powershell
git clone https://github.com/Rostave/auto-progress.git
cd auto-progress
codex plugin marketplace add "$PWD/.agents/plugins"
codex plugin add auto-progress@auto-progress-local
```

安装或更新后，请新建一个 Codex 任务，使最新 skills 生效。

如需固定到正式版本，请先切换到对应标签，再安装插件：

```powershell
git fetch --tags
git checkout v0.3.0
codex plugin add auto-progress@auto-progress-local
```

### 从 Release ZIP 安装

不想克隆完整仓库时，从 [GitHub Releases](https://github.com/Rostave/auto-progress/releases) 下载目标版本的 `auto-progress-<version>.zip`，将 ZIP 附加到 Codex 任务，然后发送以下 prompt。若当前界面不接受 ZIP 附件，直接把 ZIP 的本地绝对路径一起发给 Codex。

```text
请安装我附加的 AutoProgress Release ZIP：

1. 检查 ZIP 内是否存在 auto-progress/.codex-plugin/plugin.json，并读取插件版本。
2. 将插件解压到版本隔离的本地目录，不要修改当前项目仓库。
3. 为这个目录创建并注册一个非默认的本地 marketplace；source.path 指向 ./plugins/auto-progress。
4. 从该 marketplace 安装 auto-progress。若同版本已经安装，则安全地重新安装；不要删除其他版本。
5. 完成后告诉我安装的插件版本和 marketplace 名称，并提醒我新建 Codex 任务使插件生效。
```

Codex 会处理解压、marketplace 配置和插件安装。安装完成后，请按提示新建任务。

## 第一次配置

在需要管理的 Unity 项目中对 Codex 说：

```text
使用 $configure-auto-progress init 初始化当前 Unity 项目。
```

配置保存在 `.codex/auto-progress.toml`。初始化后，请至少确认：

- `project.base_branch`：工作来源和 PR 目标分支。
- `project.timezone`：项目使用的 IANA 时区，例如 `Asia/Shanghai`。
- `validation.steps`：项目真实可用的 C# 构建命令；不要保留模板中的 `YourUnityProject.sln`。
- `paths.allowed` 与 `paths.excluded`：AutoProgress 可修改和禁止修改的范围。
- `schedule`：定时实现任务的运行窗口。
- `unity_mcp.mode`：选择 `disabled`、`optional` 或 `required`。

推荐先验证配置和环境：

```text
使用 $configure-auto-progress validate 验证当前配置和仓库环境。
```

## 日常使用

### 1. 发现改进项

```text
使用 $discover-improvements 为当前项目补充改进池。
```

也可以限制审查范围：

```text
使用 $discover-improvements 检查 Assets/Scripts/Combat。
```

发现任务只审查代码并创建候选文档 Draft PR，不修改产品代码、不运行 Unity，也不占定时实现的每日额度。候选 PR 经人工合并到基准分支后，改进项才进入权威队列。

### 2. 手动执行实现任务

```text
使用 $maintain-project 现在执行一次 AutoProgress 实现任务。
```

手动实现不占每日额度。AutoProgress 会按以下顺序选择工作：

1. 恢复未完成的实现或处理已有实现 PR 的审查反馈。
2. 修复基准分支已有的 C# 编译错误。
3. 执行最高优先级的人工指令改进项。
4. 选择最多 3 个相互兼容的普通改进项。
5. 没有安全且有价值的工作时正常跳过。

每次交付都会创建 Draft PR；AutoProgress 不会自动合并。

### 3. 添加人工指定任务

```text
使用 $queue-directed-improvement 创建人工指令改进项：
优化角色存档加载过程；验收条件是……；允许修改范围是……。
```

请尽量提供期望结果、验收条件、允许或禁止路径、优先级，以及确实需要的规则豁免。该入口只创建任务，不会在同一次调用中实现、提交或创建实现 PR。

### 4. 拒绝不合适的改进

拒绝一个已有改进项：

```text
使用 $record-improvement-rejection 拒绝 IMP-2026.08.01-xxxxxxxx，
理由是：该模块必须保持无反射实现。
```

AutoProgress 会为该 `IMP-ID` 创建或补全拒绝记录，并通过单独的 Draft PR 交付。它不会仅因为候选 PR 被关闭或未合并就推断“已拒绝”。

若要提前阻止一类方案，可编辑：

```text
docs/auto-progress/rejection-rules.md
```

每条规则使用唯一的 `REJ-<kebab-case>` ID，说明不希望提出的方案模式、原因和适用范围。人工指令任务仍可使用明确声明的豁免，但不能绕过安全、凭据、人工合并或冲突处理规则。

### 5. 查看、暂停或恢复

```text
使用 $configure-auto-progress status 查看 AutoProgress 状态。
使用 $configure-auto-progress pause 暂停后续定时任务。
使用 $configure-auto-progress resume 恢复后续定时任务。
```

暂停不会中断已经开始的任务。恢复后不会自动补跑错过的维护日。

## 从 0.2.0 升级到 0.3.0

先更新并重新安装插件，然后新建 Codex 任务：

```powershell
git fetch --tags
git checkout v0.3.0
codex plugin add auto-progress@auto-progress-local
```

进入原 Unity 项目后执行：

```text
使用 $configure-auto-progress migrate 迁移当前项目配置。
```

迁移会先展示完整差异，只有确认后才写入 `.codex/auto-progress.toml`。它不会自动提交配置或创建迁移 PR。

schema v4 会新增：

- `[[repository_guidance.documents]]`：分别记录仓库规范文档及其 Git blob SHA。
- `paths.rejection_rules`：预防性拒绝规则文件路径。

旧的 `IMP-ID.md` 文件无需批量改名；AutoProgress 在正常交付触及对应改进项时才迁移其状态后缀。

## Unity MCP

Unity MCP 用于连接已经打开且项目路径匹配的 Unity Editor。推荐从 `optional` 模式开始：

```toml
[unity_mcp]
mode = "optional" # disabled | optional | required
adapter = "coplaydev-unity-mcp"
transport = "streamable_http"
url = "http://127.0.0.1:8080/mcp"
expected_project_root = "."
connect_timeout_seconds = 5
operation_timeout_minutes = 10
```

- `disabled`：不连接 Unity MCP，仅执行配置的 C# 验证。
- `optional`：连接失败时退回 C# 验证，并保持 PR 为 Draft。
- `required`：无法连接匹配的 Editor 或无法完成验证时阻止交付。

端点必须是 `127.0.0.1`、`localhost` 或 `::1`。配置中不要保存令牌、凭据或其他机器专属秘密。

只有匹配的 Unity Editor 实际完成刷新且 C# 编译通过时，PR 才可自动标记 Ready；否则会保持 Draft 并注明：

```text
未经 Unity 编译测试
```

## 如何理解改进项状态

- `--queued.md`：已进入权威队列，等待实现。
- `--implemented.md`：AutoProgress 已成功交付实现 Draft PR，不代表 PR 已合并。
- `--cancelled.md`：该改进项已取消。

状态以仓库外的本地账本事件为准。运行中间状态同样保存在 Codex 状态目录，不会写入 Unity 项目的 `.git` 或工作树。

## 安全边界与限制

- 只向配置的基准分支创建 PR。
- 所有 PR 默认是 Draft，永不自动合并。
- Git 冲突必须人工解决；自动任务不会执行 merge、rebase、force-push、stash、reset 或 clean。
- 定时实现任务每天最多占用一次额度；人工任务不限次数，但仍受未完成运行、现有 PR 和工作区状态等门禁约束。
- 没有合格工作时允许跳过，不创建空提交。
- 一个受管项目路径只应对应一个 AutoProgress 配置和一个运行来源。
- 不支持同一项目的多个 clone 同时运行 AutoProgress，也不提供跨实例锁。

## 常见问题

### 为什么发现任务没有修改代码？

发现和实现是两个独立阶段。发现任务只提交候选文档；候选 PR 合并后，后续 `$maintain-project` 才能选择并实现它们。

### 为什么实现 PR 一直是 Draft？

这是默认安全策略。若 Unity MCP 没有完成匹配 Editor 的刷新与编译验证，PR 还会明确显示“未经 Unity 编译测试”。

### `implemented` 是否表示已经上线？

不是。它只表示实现 Draft PR 已成功交付。是否合并、何时发布仍由人工决定。

### 中断后是否应该重新运行命令？

可以再次调用 `$maintain-project`。AutoProgress 会优先核对检查点、已有提交、远端分支和 PR，再决定继续步骤；不要手工重写历史或 force-push。

## 更多文档（使用 Grill-with-Docs 维护）

- [完整运行策略](docs/auto-progress/operating-policy.md)
- [领域术语与项目上下文](CONTEXT.md)
- [架构决策记录](docs/adr/)
