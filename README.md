# HarmonyOS Code Benchmark 构造工具

本仓库面向 HarmonyOS 开源项目构造 code benchmark。整体流程先从 GitHub 搜索、过滤并持久化候选仓库，再基于真实仓库快照生成可复现、可验证的代码任务 instance。

项目分为两个主要部分：

| 部分 | 状态 | 目标 |
| --- | --- | --- |
| 1. GitHub Repo Filter | 已实现 | 搜索并筛选适合构造 benchmark 的 HarmonyOS 仓库 |
| 2.1 ArkTS AST 错误实例构造 | 已实现 | 基于 AST 自动注入错误并生成修复任务 instance |
| 2.2 接口/基类实现补全实例构造 | 已实现 | mask 已有实现文件，并以恢复原实现的 patch 作为 gold label |
| 2.3 应用迁移实例构造 | 进行中 | 已实现基于 ArkTS AST 的 `android.*` 调用初筛 |
| 2.4 仓库 Issue 解决实例构造 | 规划中 | 基于真实 Issue 构造问题修复任务 |

## 环境与依赖

根项目要求 Python 3.10 及以上版本，当前所有核心模块只使用 Python 标准库，因此根目录 [`pyproject.toml`](pyproject.toml) 中的 `dependencies = []` 是有意为空。`setuptools>=68` 是构建依赖，不是运行时依赖。

Git、GNU `patch`、WSL 和 HarmonyOS SDK 等属于按场景使用的外部工具，不能通过 Python `dependencies` 安装。`src/SWE-bench/` 也是拥有独立 `pyproject.toml` 的子项目，其第三方依赖只用于 2.4 流程，不随根项目安装。完整的安装命令、依赖边界和环境矩阵见 [环境与依赖文档](ENVIRONMENT.md)。

推荐的根项目安装方式：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools
python -m pip install -e .
```

## 1. GitHub Repo Filter

GitHub Repo Filter 按关键字、仓库名、描述、README、主要语言、topic、owner/org 等条件搜索 GitHub 仓库，再用本地配置过滤创建时间、最近更新时间、stars、forks、license 等字段，最后把命中的仓库元数据以 JSONL 持久化到本地。该阶段只保存元数据，不 clone 仓库。

实现直接使用 GitHub 官方 Repository Search API 和搜索限定符，不依赖第三方仓库搜索项目，便于固定过滤条件和输出格式。

参考：

- GitHub repository search qualifiers: https://docs.github.com/en/search-github/searching-on-github/searching-for-repositories
- GitHub REST Search API: https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28#search-repositories

### 1.1 功能

- 搜索形式天然支持 `{owner}/{repo}`，输出每行打印 `full_name`。
- 支持关键字、搜索字段、主要语言、topic、owner、org、fork、archived、sort/order、max_results。
- 支持按 `created` 日期拆分查询，绕开单个 GitHub Search 查询最多只能访问前 1000 条结果的限制。
- 支持本地二次过滤：创建时间、更新时间、push 时间、stars、forks、语言、owner、topic、license。
- JSONL 持久化到本地，默认按 `full_name` 去重并更新已有记录。
- 只使用 Python 标准库，无运行时依赖。

### 1.2 快速开始

```powershell
python run.py --config config.json --dry-run --show-query
```

实际搜索并保存：

```powershell
$env:GITHUB_TOKEN="ghp_xxx"
python run.py --config config.json
```

也可以使用配置文件：

```powershell
python run.py --config config.example.json
```

未设置 `GITHUB_TOKEN` 时也能访问公开搜索接口，但 GitHub 未认证请求的 rate limit 更低。

### 1.3 配置

`config.example.json` 包含完整示例：

```json
{
  "search": {
    "keywords": ["ArkTS"],
    "in_fields": ["name", "description", "readme"],
    "language": "",
    "per_page": 100,
    "max_results": null,
    "created_split": {
      "enabled": true,
      "start": "2024-01",
      "end": "",
      "interval": "month"
    }
  },
  "filters": {
    "created_after": "2024-01-01",
    "updated_after": "",
    "min_stars": null,
    "languages": [],
    "exclude_archived": true,
    "exclude_forks": true
  },
  "output": {
    "path": "data/repositories.jsonl",
    "dedupe": true
  }
}
```

常用命令行覆盖项：

```powershell
python run.py `
  --keyword ArkTS `
  --in-field name `
  --in-field description `
  --in-field readme `
  --created-after 2024-01-01 `
  --max-results 200 `
  --output data/arkts-repos.jsonl
```

安装为命令后也可以使用：

```powershell
python -m pip install -e .
github-repo-filter --config config.example.json
```

### 1.4 渐进式拉取

CLI 现在按页渐进式拉取 GitHub Search API。每页数量由 `--per-page` 或配置里的 `search.per_page` 控制，默认 100，GitHub API 单页最大也是 100。

总拉取量由 `--max-results` 或配置里的 `search.max_results` 控制。默认值为 `null`，表示尽量拉取全部可访问结果；受 GitHub Search API 限制，单个查询最多只能访问前 1000 条结果。

当前 `config.json` 会生成从 `2024-01-01` 到今天的月度 created 分片，例如：

```text
ArkTS in:name,description,readme fork:false archived:false created:2024-01-01..2024-01-31
ArkTS in:name,description,readme fork:false archived:false created:2024-02-01..2024-02-29
```

GitHub 搜索大小写不敏感，所以 `ArkTS`、`arkts`、`ARKTS` 会按同一关键词处理。`in:name,description,readme` 表示关键词出现在仓库名、描述或 README 中任意一个位置即可。

每一页请求完成后会立刻执行本地过滤；只要这一页有命中记录，就马上写入 JSONL。默认按 `full_name` 去重并更新已有记录。进度条默认开启，可用 `--no-progress` 关闭。

```powershell
python run.py --config config.json --per-page 50 --max-results 300
```

### 1.5 输出 JSONL

每行是一条仓库元数据，例如：

```json
{"full_name":"owner/repo","owner":"owner","repo":"repo","html_url":"https://github.com/owner/repo","language":"Python","stargazers_count":123,"created_at":"2023-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:00Z"}
```

默认 `dedupe=true` 时，相同 `full_name` 会被新搜索结果更新。使用 `--no-dedupe` 可以改为追加写入。

### 1.6 后处理筛选

拉取完成后可以对 `data/repositories.jsonl` 继续做顺序筛选：

```powershell
python filter_repositories.py data/repositories.jsonl
```

默认筛选步骤：

- `stars > 10`，输出 `data/repositories_stars_gt10.jsonl`
- 在上一步结果基础上筛 `updated_at >= 2026-01-01`，输出 `data/repositories_stars_gt10_updated_after2026.jsonl`

筛选输出是覆盖式写入，重复运行不会保留旧结果。

对 `data/repositories_harmony.jsonl` 可以使用 `harmony` 筛选流程：

```powershell
python filter_repositories.py data/repositories_harmony.jsonl --pipeline harmony
```

该流程按顺序执行：

- `stargazers_count > 10`，输出 `data/repositories_harmony_stars_gt10.jsonl`
- 在上一步结果基础上筛 `language == "TypeScript"`，输出 `data/repositories_harmony_stars_gt10_language_typescript.jsonl`

本次筛选结果：

```text
repositories_harmony.jsonl: 64180
repositories_harmony_stars_gt10.jsonl: 1502
repositories_harmony_stars_gt10_language_typescript.jsonl: 253
```

校验条件：

```text
stars_all_gt10 True
typescript_all_gt10 True
typescript_all_language True
typescript_unique_full_name True
```

#### HarmonyOS/ArkTS 项目结构筛选

`language == "TypeScript"` 只能筛选 GitHub 识别出的主语言，搜索关键词中的 `Harmony` 也可能表示与 HarmonyOS 无关的普通英文概念。为了排除普通 TypeScript 项目，可以继续通过 GitHub API 检查仓库文件树：

```powershell
python filter_harmony_repositories.py `
  data/repositories_harmony_stars_gt10_language_typescript.jsonl `
  --workers 16 `
  --timeout 45
```

脚本优先从环境变量 `GITHUB_TOKEN` 读取 Token，未设置时会读取当前目录 `.env` 中的同名配置。默认输出：

```text
data/repositories_harmony_stars_gt10_language_typescript_harmonyos_arkts.jsonl
```

结构筛选使用以下证据：

- ArkTS 源码：仓库文件树中存在 `.ets` 文件。
- HarmonyOS 构建标记：`build-profile.json5`、`hvigorfile.ts`、`oh-package.json5`、`AppScope/app.json5`、`module.json5`、`hvigor-config.json5`。
- 辅助元数据关键词：`ArkTS`、`ArkUI`、`HarmonyOS`、`OpenHarmony`、`OHOS`、`鸿蒙`。单独出现泛化关键词 `Harmony` 不作为 HarmonyOS 证据。

每条保留记录会新增 `harmony_project` 字段，包含置信等级、`.ets` 文件数量、命中的构建标记、元数据关键词和 GitHub 文件树是否被截断。置信等级规则如下：

- `high`：存在 `.ets` 文件，并且命中至少两个 HarmonyOS 构建标记。
- `medium`：存在 `.ets`、至少一个构建标记和明确的 HarmonyOS 元数据关键词；或者命中至少三个构建标记和明确的元数据关键词。
- `low`：存在 `.ets` 和明确的 HarmonyOS 元数据关键词，但没有构建标记，通常属于源码示例、编辑器插件或不完整工程。
- `none`：不满足以上条件，通常是因为泛化的 `Harmony` 关键词被搜索命中。

默认只保留 `medium` 和 `high`。如需包含源码示例，可以使用 `--min-confidence low`；如需保存未通过记录及其证据，可以使用 `--rejected-output <path>`。

2026-08-03 的筛选结果：

```text
repositories_harmony_stars_gt10_language_typescript.jsonl: 253
repositories_harmony_stars_gt10_language_typescript_harmonyos_arkts.jsonl: 122
high confidence: 121
medium confidence: 1
GitHub tree inspection error: 1
```

本次有一个仓库因为 GitHub 文件树响应不完整而未能检查，未计入 122 个保留结果；重新运行命令会再次尝试该仓库。

#### GPT 仓库语义复核

对拥有 merged PR 的候选仓库继续进行仓库级语义复核，输入文件为：

```text
data/repositories_harmony_stars_gt10_language_typescript_harmonyos_arkts_PR_merged.jsonl
```

复核同时检查 GitHub 仓库描述、README 和根目录，采用以下判定规则：

- 仓库主体是 HarmonyOS 应用、组件库、SDK、开发工具或迁移工具时保留。
- 跨平台或 monorepo 仓库只要包含 README 明确说明且实际维护的 HarmonyOS 客户端、SDK 或子项目，也视为与 HarmonyOS 相关并保留。
- 仅出现泛化的 `Harmony` 关键词、偶然的 ArkTS 文件、生成产物或不构成实际 HarmonyOS 项目的示例时剔除。

2026-08-03 的语义复核结果：

```text
输入仓库：34
保留仓库：34
剔除仓库：0
```

全部候选均能确认 HarmonyOS 归属。包括 `cropflre/nowen-note` 和 `botiverse/hands` 这类主体为跨平台产品的仓库，其 README 和目录中也分别存在实际维护的 HarmonyOS 客户端或 ArkTS SDK，因此没有作为无关仓库删除。过滤结果保存至：

```text
data/repositories_harmony_stars_gt10_language_typescript_harmonyos_arkts_PR_merged_gpt_filter.jsonl
```

### 1.7 诊断空结果

如果 GitHub 搜索有返回，但 JSONL 仍然没有记录，可以打印前几条本地过滤原因：

```powershell
python run.py --config config.json --no-write --show-query --explain-filtered 10
```

例如 `language:ArkTS` 目前可能不会被 GitHub 按主语言严格识别，API 返回的仓库主语言可能是 `TypeScript`、`JavaScript`、`Python` 等。此时本地过滤会因为 `language_not_allowed` 拒绝这些仓库。建议把 `ArkTS` 放到 `keywords`，把 `language` 改成 GitHub 能识别的主语言，例如 `TypeScript`。

## 2. Instance 生成

Instance 生成阶段以筛选出的真实 HarmonyOS 仓库为基础，保存任务所需的仓库快照、问题描述、参考补丁和验证信息。不同任务类型共享可复现、原仓库隔离和结果可验证的基本要求。

### 2.1 ArkTS AST 错误实例构造（已实现）

#### ArkTS 语法树解析

工作区提供一个通用 HarmonyOS ArkTS/ETS 结构解析脚本，不需要写入被解析的目标仓库：

```powershell
wsl bash -lc "python3 tools/parse_arkts_syntax_tree.py test_project/Wechat_HarmonyOS"
```

默认扫描 `.ets` 和 `.ts` 文件，跳过 `.git`、`build`、`node_modules`、`oh_modules` 等目录，输出到工作区：

- `syntax_trees/<repo-name>_syntax_tree.jsonl`
- `syntax_trees/<repo-name>_syntax_tree_summary.json`

每个 JSONL 记录对应一个源码文件，包含 imports、文件级 tree、节点行号、decorators、modifiers、signature 和 metrics。当前解析器面向 ArkTS/ArkUI 项目做轻量结构化解析，识别 `struct`、`class`、`interface`、`enum`、`function`、`property`、`method`、ArkUI `ui_component` 和 callback 节点。

#### 错误实例构造

项目可以基于已提取的 ArkTS 抽象语法树，自动筛选高调用出度且被多个跨文件函数调用的目标函数。生成器会枚举条件取反、比较符替换、逻辑符替换、数值边界、赋值运算符替换和独立副作用调用删除六类单点 mutation，通过 `bug.patch` 表达错误，并生成隔离的变异仓库快照、`fix.patch` 和包含调用影响证据的 `instance.json`。生成目录不修改原始 clone。

候选选择采用可复现的“算子分层 + 影响面排序 + seed 轮转”策略，避免所有实例集中在同一函数或同一种错误。默认 seed 由仓库名和 commit 稳定派生，也可以用 `--mutation-operator` 和 `--selection-seed` 显式控制。面向求解者的任务描述只呈现受影响调用路径和错误类别，具体变异前后文本仅保存在评测元数据中，避免直接泄露答案。

完整的分析规则、注入流程、命令参数、instance 格式、验证方式和已知边界见 [ArkTS AST 错误实例构造文档](src/arkts_syntax_tree/README.md)。

### 2.2 接口/基类实现补全实例构造

生成器会发现 `interface` 或拥有派生实现的基类，并要求至少存在两个实现/使用文件；其中支持显式的 `implements`/`extends`，接口还支持 ArkTS 常见的结构化使用。生成时选择其中一个实现文件进行整文件 mask，要求智能体参考抽象定义和其他实现文件，在原路径补全被遮蔽的实现。

列出 `test_project` 中的候选抽象节点：

```powershell
PYTHONPATH=src python tools/build_feature_instance.py `
  test_project/Wechat_HarmonyOS --list
```

构造实现补全 instance：

```powershell
PYTHONPATH=src python tools/build_feature_instance.py `
  test_project/Wechat_HarmonyOS
```

验证开发后的 instance：

```powershell
cd <独立仓库副本>
patch -p1 -i ../instances/feature_implement/<instance-id>/mask.patch

PYTHONPATH=src python tools/verify_feature_instance.py \
  instances/feature_implement/<instance-id> \
  <独立仓库副本>
```

生成的目录包含 `mask.patch`、`gold.patch`、`syntax_tree.jsonl` 和 `instance.json`，不保存仓库副本。默认 instance ID 为 `<仓库名>-<随机 UUID>`，默认保存在 `instances/feature_implement/`。评测时在独立仓库副本上应用 `mask.patch`，智能体完成实现；`gold.patch` 是从 mask 内容恢复原实现的 gold label。原始仓库不会被修改，错误修复实例则统一保存在 `instances/error_fix/`。

### 2.3 应用迁移实例构造（进行中）

面向 HarmonyOS 应用、SDK、API 或版本升级场景构造迁移任务。最终 instance 计划记录迁移前快照、目标平台或版本约束、迁移要求和验收方式，覆盖接口替换、配置调整、依赖升级和兼容性修改。

#### 第一步：基于语法树初筛 Android 调用

`arkts_syntax_tree` 解析器现在会在每个文件的 JSONL 记录中增加 `calls` 字段。每条调用表达式包含规范化后的 `callee`、行号和列号；提取前会掩盖注释、普通字符串和模板字符串，因此这些非代码区域中的 `android.*` 文本不会形成调用节点。

先解析目标仓库，再只使用生成的语法树 JSONL 执行检测：

```powershell
python tools/parse_arkts_syntax_tree.py test_project/legado-Harmony
python tools/detect_android_calls.py `
  syntax_trees/legado-Harmony_syntax_tree.jsonl
```

也可以将结构化检测报告保存到文件：

```powershell
python tools/detect_android_calls.py `
  syntax_trees/legado-Harmony_syntax_tree.jsonl `
  --output syntax_trees/legado-Harmony_android_calls.json
```

检测器严格匹配调用目标以 `android.` 开头的调用，例如 `android.app.Activity.start()`；可选链会被规范化，如 `android?.content?.open()` 记录为 `android.content.open`。每个命中还会利用语法树的起止行定位所在的 class、struct、function、method 或 callback 作用域。旧版 JSONL 没有 `calls` 字段时工具会要求重新解析，避免把缺失分析数据误判为零命中。

#### legado-Harmony 验证

`test_project/legado-Harmony` 当前 `main` 分支在提交 `9421676` 停止开源后已删除应用源码，工作区只剩一个 `hvigorfile.ts`。为了验证真实迁移代码，本次从同一测试仓库导出删除源码前的提交 `af6fda1`，不切换或修改测试仓库工作区：

```powershell
$source = Join-Path $env:TEMP "legado-Harmony-af6fda1"
$archive = Join-Path $env:TEMP "legado-Harmony-af6fda1.zip"

git -c safe.directory=D:/Project/HarmonyOS-benchmark/test_project/legado-Harmony `
  -C test_project/legado-Harmony archive `
  --format=zip --output=$archive af6fda1
Expand-Archive -LiteralPath $archive -DestinationPath $source

python tools/parse_arkts_syntax_tree.py $source `
  --output syntax_trees/legado-Harmony_af6fda1_syntax_tree.jsonl `
  --summary syntax_trees/legado-Harmony_af6fda1_syntax_tree_summary.json
python tools/detect_android_calls.py `
  syntax_trees/legado-Harmony_af6fda1_syntax_tree.jsonl
```

2026-08-03 的验证结果：

```text
files: 346
imports: 1277
nodes: 10935
calls_scanned: 18683
android_call_count: 0
files_with_android_calls: []
```

源码中出现的 Android 文本位于注释、User-Agent 字符串和界面选项字符串中，均不属于调用表达式，因此零命中符合预期。专项测试可通过以下命令运行：

```powershell
python -m unittest tests.test_syntax_tree tests.test_migration -v
```

#### 批量扫描候选仓库

以下脚本用于扫描 `repositories_harmony_stars_gt10_language_typescript_PR_merged.jsonl` 中的全部仓库：

```powershell
python tools/scan_android_repositories.py
```

默认行为如下：

- 从 `data/repositories_harmony_stars_gt10_language_typescript_PR_merged.jsonl` 读取仓库；
- 使用仓库的 `default_branch` 执行 `git clone --depth 1 --single-branch --no-tags`；
- clone 到系统临时目录中的独立子目录，解析 `.ets` 和 `.ts` 后执行 `android.*(...)` 检测；
- 无论 clone、解析或检测成功还是失败，都在单仓库任务结束时删除该临时子目录；
- 默认并行处理 4 个仓库，每条结果完成后立即写入 `data/repositories_harmony_stars_gt10_language_typescript_PR_merged_android_calls.jsonl`；
- 再次运行时跳过输出中 `status=ok` 的仓库并重试错误记录，可以从中断位置继续。

先用少量仓库验证网络和环境：

```powershell
python tools/scan_android_repositories.py `
  --max-repos 2 `
  --workers 1 `
  --clone-root .tmp/android-call-scan `
  --overwrite
```

完整扫描并自定义超时、并发数和输出位置：

```powershell
python tools/scan_android_repositories.py `
  data/repositories_harmony_stars_gt10_language_typescript_PR_merged.jsonl `
  --output data/repositories_android_calls.jsonl `
  --workers 4 `
  --clone-timeout 300
```

每条输出记录包含仓库名、实际扫描 commit、状态、文件数、调用总数、`android_call_count`、命中文件及带源码位置和作用域的调用列表。单个仓库失败时记录 `error.stage` 和 `error.message`，批处理继续运行；全部完成但存在错误时脚本退出码为 2。

2026-08-03 使用 `--max-repos 1 --workers 1` 完成联网烟雾测试：`751496032/DSBridge-HarmonyOS` 在 commit `3de1a9f382411f8508c74c37bfc4f638c8573a6a` 上解析 28 个文件、719 个调用表达式，`android_call_count=0`，任务结束后临时 clone 目录为空。

随后对 `repositories_harmony_stars_gt10_language_typescript.jsonl` 执行批量扫描。该文件实际包含 253 条仓库记录；按操作要求停止最后一个耗时仓库 `MinamiJogen/HarmonyOS-EhViewer` 后，最终结果为：

```text
completed repositories: 252
successful repositories: 252
errors: 0
ArkTS/TS files scanned: 39668
call expressions scanned: 2299939
repositories with android.* calls: 0
android.* calls: 0
not scanned: MinamiJogen/HarmonyOS-EhViewer
temporary clone directories remaining: 0
```

结果保存在 `data/repositories_harmony_stars_gt10_language_typescript_android_calls.jsonl`。其中包含 252 条唯一仓库记录及各自的扫描 commit；由于最后一个仓库未生成结果，该文件不应视为完整 253 仓库扫描结果。

不执行 `git clone` 的替代方案是下载 GitHub/codeload 的 branch zip archive。它仍需临时解压源码，但不下载 `.git` 和历史对象，通常比浅克隆占用更少；如果希望完全不落盘，也可以使用 Git Trees API 获取文件树，再通过 Git Blobs API 只读取 `.ets`/`.ts` 内容并在内存解析。后者的 API 请求数和 rate limit 成本更高，对 34 个仓库批量扫描时，浅克隆或 archive 通常更稳定。GitHub Code Search 不保证完整索引，也无法可靠排除注释和字符串，不适合作为本检测的替代。

当前初筛只覆盖 `.ets` 和 `.ts` 中直接以 `android.` 开头的静态调用，不展开别名、动态属性、反射，也不扫描 Java/Kotlin 源码。后续构造迁移 instance 时，需要在此结果上继续关联 Android API 与 HarmonyOS 替代 API、迁移前快照及可执行验收条件。

### 2.4 仓库 Issue 解决实例构造（规划中）

面向公开仓库中的真实 Issue 和对应 Pull Request 构造问题修复任务。计划关联 Issue 描述、PR 的 base commit、gold patch、测试变更和验证条件，使生成的 instance 能够复现并检查 Issue 是否被正确解决。目前尚未实现 HarmonyOS 专用的自动生成脚本，后续将复用 `src/SWE-bench/swebench/collect/` 中的 PR 采集和 instance 构造逻辑。

#### 过滤后仓库的 PR 分布

2026-08-03 使用 GitHub GraphQL API 对以下最新过滤结果进行统计：

```text
data/repositories_harmony_stars_gt10_language_typescript_harmonyos_arkts.jsonl
```

该文件包含 122 个 high/medium 置信的 HarmonyOS/ArkTS 仓库，全部能够正常查询 PR。PR 状态分布如下：

| 状态 | PR 数量 | 占比 |
| --- | ---: | ---: |
| Open | 32 | 1.9% |
| Closed、未合并 | 177 | 10.5% |
| Merged | 1,469 | 87.5% |
| 合计 | 1,678 | 100% |

仓库级统计：

- 有 PR 的仓库：41（33.6%）。
- 没有 PR 的仓库：81（66.4%）。
- 每仓库平均 PR 数：13.75；中位数：0。
- P75、P90、P95：1、5.9、52.3。
- 单仓库最大 PR 数：401。

总 PR 数量的仓库分布：

| 单仓库 PR 数 | 仓库数 | 占比 |
| --- | ---: | ---: |
| 0 | 81 | 66.4% |
| 1-5 | 28 | 23.0% |
| 6-10 | 4 | 3.3% |
| 11-25 | 2 | 1.6% |
| 26-50 | 0 | 0% |
| 51-100 | 2 | 1.6% |
| 101-250 | 2 | 1.6% |
| 251-500 | 3 | 2.5% |

按 merged PR 数量统计，可用于后续 Issue/PR instance 构造的仓库规模为：

| 条件 | 仓库数 |
| --- | ---: |
| merged PR >= 1 | 34 |
| merged PR >= 5 | 11 |
| merged PR >= 10 | 9 |
| merged PR >= 25 | 7 |
| merged PR >= 50 | 6 |

merged PR 数量最多的仓库：

| 仓库 | Merged | PR 总数 | Open | Closed、未合并 |
| --- | ---: | ---: | ---: | ---: |
| `botiverse/hands` | 390 | 401 | 4 | 7 |
| `mgz0227/legado-Harmony` | 295 | 342 | 5 | 42 |
| `cropflre/nowen-note` | 238 | 312 | 1 | 73 |
| `Your-USTC/DailyNic_HMOS` | 176 | 179 | 0 | 3 |
| `ohosvscode/arkTS` | 171 | 203 | 13 | 19 |
| `Edge-Music/Community` | 63 | 72 | 0 | 9 |
| `ibestservices/ibest-ui` | 49 | 54 | 0 | 5 |
| `ibestservices/ibest-ui-v2` | 16 | 20 | 0 | 4 |
| `netease-kit/nim-uikit-harmony` | 15 | 15 | 0 | 0 |
| `wuba/omni-ui` | 7 | 7 | 0 | 0 |

这些 merged PR 只是初始候选，不等同于可直接使用的 benchmark instance。按照 SWE-bench 的筛选逻辑，PR 还需要关联至少一个被解决的 Issue，并能提取非空的问题描述和 gold patch；用于评测的数据还需要包含测试代码变更。由于 122 个仓库中只有 34 个拥有 merged PR，后续可以优先从 merged PR 不少于 10 个的 9 个仓库开始采集，再检查 Issue 关联、补丁内容和测试变更。

#### legado-Harmony 构造试验结论

已复用 `src/SWE-bench/swebench/collect/` 对 `mgz0227/legado-Harmony` 进行实际采集和 instance 构造试验，结果保存在：

```text
instances/issue_resolve/legado-harmony-pr-349/
```

本次选择 merged PR #349 `修复错误颜色值导致的UI显示问题`。其 upstream base commit 为 `1c824edaa798635dceeac4de299093e707316bdd`，对应快照恢复在 `test_project/legado-Harmony`。生成内容包括：

- `instance.json`：保留 SWE-bench 核心字段，并记录兼容性降级、commit 和 artifact 信息。
- `fix.patch`：从 PR diff 提取的 gold patch，修改 `ReaderPage3.ets` 中 3 个错误颜色值。
- `test.patch`：原 PR 没有测试变更，因此为空。
- `task.md`：从 PR 标题扩展的问题描述。
- `verify.py` 和 `validation.json`：静态验收规则及验证结果。
- `collection/`：原始 PR JSONL、原始 SWE-bench 空输出和 ArkTS 解析摘要。

验证结果：

```text
gold patch apply check: passed
fixed static verifier: passed
ArkTS/TS parsed files: 346
syntax tree imports: 1274
syntax tree nodes: 10932
snapshot reverse apply and clean check: passed
```

严格准入检查显示，该仓库的 295 个 merged PR 中，没有 PR 使用 SWE-bench 支持的 closing Issue 方式关联独立 Issue；只有 3 个 PR 包含测试文件变更，这 3 个 PR 同样没有关联 Issue。PR #349 的 `resolved_issues=[]`，所以原始 `build_dataset` 按预期没有生成候选。

为验证剩余流程，本次没有伪造 Issue 编号，而是明确采用“PR 标题作为 `problem_statement`”的兼容降级，并使用静态脚本替代缺失的测试补丁。当前环境也没有 DevEco Studio、`ohpm` 或可执行的 hvigor wrapper，因此未运行原生 HarmonyOS 构建。结论是：该实例可用于验证 HarmonyOS PR 采集、gold patch 构造和静态验收流程，但不能作为严格满足真实 Issue 关联和测试补丁要求的 SWE-bench 评测实例。完整过程见 `src/SWE-bench/README.md`。

## 开发与测试

```powershell
python -m unittest discover -s tests
```
