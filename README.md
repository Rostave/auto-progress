# AutoProgress

AutoProgress 是一个面向 Unity C# 项目的 Codex 插件。它把“寻找改进项”和“实现改进项”拆成两类受控任务，在每天有限的执行额度内复用环境检查、构建验证和 GitHub PR 流程，并始终保留人工审查与合并权。

## 核心边界

- 基准分支由人工配置，也是唯一允许的 PR 目标分支。
- 所有 PR 默认创建为 Draft，永不自动合并。
- Git 冲突只能由人工解决；自动任务不执行 merge、rebase、force-push、stash、reset 或 clean。
- 每个维护日最多启动一种 AutoProgress 任务类型。
- 没有安全且有价值的工作时允许跳过，不创建空 commit。
- Unity 未经实际刷新与编译验证时，PR 必须明确标注“未经 Unity 编译测试”。
- 本地运行账本按月追加保存，默认永久保留，只能由人工确认清理。

## 插件提供的入口

- `$maintain-project`：执行一次实现维护批次、人工提前运行或待恢复工作。
- `$discover-improvements`：人工触发有界代码审查并创建候选项文档 PR，不实现代码或运行 Unity。
- `$configure-auto-progress`：初始化、迁移、验证、暂停、恢复、查看状态或导出状态。
- `$queue-directed-improvement`：人工录入少量强制改进项，不会自动执行录入入口。

后面三个管理或发现入口只能由人工显式调用。

## 安装

1. 克隆本仓库。
2. 将仓库内的本地 marketplace 加入 Codex：

```powershell
codex plugin marketplace add "<path-to-auto-progress>/.agents/plugins"
```

3. 安装插件：

```powershell
codex plugin add auto-progress@auto-progress-local
```

4. 新建一个 Codex 任务，使新安装的 skill 生效。

更新本地克隆后，再次运行第 3 步即可刷新插件缓存版本。

## 初始化项目

在需要管理的 Unity 仓库中显式调用：

```text
使用 $configure-auto-progress init 初始化当前 Unity 项目。
```

项目策略保存在：

```text
.codex/auto-progress.toml
```

至少需要人工确认：

- `project.base_branch`：例如 `feature/your-base-branch`。
- `project.timezone`：例如 `Asia/Shanghai`。
- `validation.steps`：项目真实可用的 C# 构建命令，例如对 `<your-unity-project>.sln` 执行 `dotnet msbuild`。
- `paths.allowed` 与 `paths.excluded`。
- Unity MCP 是否启用及期望项目根目录。

模板中的 `YourUnityProject.sln` 只是描述性示例，不能直接用于真实项目。

旧版 `schema_version = 1` 必须由人工迁移：

```text
使用 $configure-auto-progress migrate 将当前项目配置迁移到 v2。
```

迁移会先展示完整差异，确认后只修改配置文件，不会自动 commit 或创建 PR。

## 寻找改进项

```text
使用 $discover-improvements 为当前项目补充改进池。
```

也可以指定配置允许范围内的关注路径。默认策略：

- 目标库存为 10 项普通自动 `queued` 改进。
- 单次最多提出 10 项。
- 初始审查 30 个 C# 文件，每轮最多扩展 15 个。
- 单次最多审查 60 个文件、合计 12,000 行源码。
- 使用 `project.max_run_minutes` 作为总时长上限，默认 60 分钟。

发现任务使用从最新远端基准分支创建的轻量 worktree，不复制 Unity `Library`，不调用 Unity MCP，也不执行 C# 构建。候选项只通过 Draft 发现 PR 提交；只有人工合入基准分支后，它们才成为权威改进池中的 `queued` 项。

发现任务一旦开始核心审查就占用当天活动额度，因此当天不再启动实现维护。

## 实现维护批次

自动调度或人工提前运行使用：

```text
使用 $maintain-project 提前执行今天的 AutoProgress 实现任务。
```

工作选择顺序：

1. 恢复已有实现分支或处理实现 PR 的审查反馈。
2. 修复远端基准分支已有的 C# 编译错误。
3. 执行最高优先级的人工指令改进项。
4. 从权威改进池选择最多 3 个兼容的普通自动改进项。
5. 没有合格工作时安全跳过。

恢复、编译修复和人工指令项默认独占一次运行。普通改进项只有在模块、范围、验证路径和总预算兼容时才能组成批次。每个改进项保留独立 ID、验收条件和 commit。

## 人工指令改进项

```text
使用 $queue-directed-improvement 创建人工指令改进项：
<描述期望结果、验收条件、允许范围与必要豁免>。
```

人工指令项完全豁免拒绝清单，但仍受 Git 安全、冲突人工处理、验证真实性和人工合并等硬规则约束。除拒绝清单外，只有明确写入该指令项的豁免才生效。

## 暂停、恢复与状态

```text
使用 $configure-auto-progress pause 暂停每日自动任务。
使用 $configure-auto-progress resume 恢复每日自动任务。
使用 $configure-auto-progress status 查看当前状态。
```

暂停不会中断已经开始的任务；若当天尚未开始，则取消当天任务。恢复后不会自动补做错过的维护日。

状态统计区分：

- 每日活动额度及其任务类型。
- 实现批次数与逐项 `delivered`、`deferred`、`reverted`、`candidate_stale`。
- 发现会话数、审查文件/行数、候选项数和零候选会话。
- 当前基准分支中的权威库存与未合并发现 PR 候选项。

`delivered` 只表示已进入实现 PR；只有随 PR 合入基准分支的 `implemented` 才表示真正完成。

## Unity 验证

实现任务复用原始 Unity 项目目录和现有 `Library`。若匹配项目根目录的 Unity Editor 已打开，任务可以通过 Unity MCP 在 checkout 后刷新并检查 C# 编译结果。

只有实际完成匹配 Editor 的刷新且 C# 编译通过时，PR 才能自动标记 Ready。否则必须保持 Draft 并显示：

```text
未经 Unity 编译测试
```

## 开发与验证

运行脚本测试：

```powershell
python -m unittest discover -s plugins/auto-progress/scripts -p "test_*.py" -v
```

校验示例配置：

```powershell
python plugins/auto-progress/scripts/auto_progress.py validate-config `
  --config plugins/auto-progress/assets/auto-progress.toml
```

插件源代码、模板和策略文档不应包含真实项目名、私人仓库地址、账号邮箱、本机绝对路径或凭据。项目专属信息只应存在于使用者自己的受管项目配置中。
