# HarmonyOS Code Benchmark 构造工具

本仓库面向 HarmonyOS 开源项目构造 code benchmark。整体流程先从 GitHub 搜索、过滤并持久化候选仓库，再基于真实仓库快照生成可复现、可验证的代码任务 instance。

项目分为两个主要部分：

| 部分 | 状态 | 目标 |
| --- | --- | --- |
| 1. GitHub Repo Filter | 已实现 | 搜索并筛选适合构造 benchmark 的 HarmonyOS 仓库 |
| 2.1 ArkTS AST 错误实例构造 | 已实现 | 基于 AST 自动注入错误并生成修复任务 instance |
| 2.2 新特性开发实例构造 | 规划中 | 基于真实仓库构造新功能开发任务 |
| 2.3 应用迁移实例构造 | 规划中 | 构造应用、API 或版本迁移任务 |
| 2.4 仓库 Issue 解决实例构造 | 规划中 | 基于真实 Issue 构造问题修复任务 |

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

项目可以基于已提取的 ArkTS 抽象语法树，自动筛选高调用出度且返回值被多个跨文件函数消费的函数，在隔离仓库快照中注入返回值错误，并生成 `bug.patch`、`fix.patch` 和包含下游消费者描述的 `instance.json`。原始 clone 不会被修改。

当前 Wechat 示例选择 `PermissionUtils.request`，将 `return isAuth` 变异为 `return false`，生成的实例位于 `instances/wechat-permission-request-return-false/`。

完整的分析规则、注入流程、命令参数、instance 格式、验证方式和已知边界见 [ArkTS AST 错误实例构造文档](src/arkts_syntax_tree/README.md)。

### 2.2 新特性开发实例构造（规划中）

面向真实 HarmonyOS 仓库构造尚未实现的新功能任务。计划从基线仓库快照、功能需求、受影响模块和验收条件生成 instance，用测试或其他可执行检查判断功能是否正确实现。目前尚未实现自动生成脚本。

### 2.3 应用迁移实例构造（规划中）

面向 HarmonyOS 应用、SDK、API 或版本升级场景构造迁移任务。计划记录迁移前快照、目标平台或版本约束、迁移要求和验收方式，覆盖接口替换、配置调整、依赖升级和兼容性修改。目前尚未实现自动生成脚本。

### 2.4 仓库 Issue 解决实例构造（规划中）

面向公开仓库中的真实 Issue 构造问题修复任务。计划关联 Issue 描述、对应基线 commit、错误复现信息、相关代码范围和验证条件，使生成的 instance 能够复现并检查 Issue 是否被正确解决。目前尚未实现自动生成脚本。

## 开发与测试

```powershell
python -m unittest discover -s tests
```
