# GitHub Repo Filter

一个轻量级 GitHub 仓库过滤工具：按关键字、主要语言、topic、owner/org 等条件搜索 GitHub 仓库，再用本地配置过滤创建时间、最近更新时间、stars、forks、license 等字段，最后把命中的仓库以 JSONL 持久化到本地。工具只保存元数据，不 clone 仓库。

实现选择：直接使用 GitHub 官方 Repository Search API 和搜索限定符，而不是依赖某个第三方仓库。这样依赖更少，也更容易把过滤条件和输出格式固定成当前项目需要的形式。

参考：

- GitHub repository search qualifiers: https://docs.github.com/en/search-github/searching-on-github/searching-for-repositories
- GitHub REST Search API: https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28#search-repositories

## 功能

- 搜索形式天然支持 `{owner}/{repo}`，输出每行打印 `full_name`。
- 支持关键字、主要语言、topic、owner、org、fork、archived、sort/order、max_results。
- 支持本地二次过滤：创建时间、更新时间、push 时间、stars、forks、语言、owner、topic、license。
- JSONL 持久化到本地，默认按 `full_name` 去重并更新已有记录。
- 只使用 Python 标准库，无运行时依赖。

## 快速开始

```powershell
python run.py --dry-run --keyword HarmonyOS --keyword benchmark --language Python --min-stars 5 --updated-after 2024-01-01 --show-query
```

实际搜索并保存：

```powershell
$env:GITHUB_TOKEN="ghp_xxx"
python run.py --keyword HarmonyOS --keyword benchmark --language Python --min-stars 5 --updated-after 2024-01-01 --output data/repositories.jsonl
```

也可以使用配置文件：

```powershell
python run.py --config config.example.json
```

未设置 `GITHUB_TOKEN` 时也能访问公开搜索接口，但 GitHub 未认证请求的 rate limit 更低。

## 配置

`config.example.json` 包含完整示例：

```json
{
  "search": {
    "keywords": ["HarmonyOS", "benchmark"],
    "language": "Python",
    "max_results": 100
  },
  "filters": {
    "created_after": "2020-01-01",
    "updated_after": "2024-01-01",
    "min_stars": 5,
    "languages": ["Python"],
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
  --keyword HarmonyOS `
  --language ArkTS `
  --min-stars 10 `
  --created-after 2022-01-01 `
  --updated-after 2025-01-01 `
  --max-results 200 `
  --output data/harmonyos-repos.jsonl
```

安装为命令后也可以使用：

```powershell
python -m pip install -e .
github-repo-filter --config config.example.json
```

## 输出 JSONL

每行是一条仓库元数据，例如：

```json
{"full_name":"owner/repo","owner":"owner","repo":"repo","html_url":"https://github.com/owner/repo","language":"Python","stargazers_count":123,"created_at":"2023-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:00Z"}
```

默认 `dedupe=true` 时，相同 `full_name` 会被新搜索结果更新。使用 `--no-dedupe` 可以改为追加写入。

## 测试

```powershell
python -m unittest discover -s tests
```
