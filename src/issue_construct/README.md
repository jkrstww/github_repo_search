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

## legado-Harmony PR instance 构造试验

2026-08-03 使用 `mgz0227/legado-Harmony` 对 `swebench.collect` 的 PR 采集和 instance 构造流程进行了兼容性测试。生成的实例位于：

```text
instances/issue_resolve/legado-harmony-pr-349/
```

### 候选筛选

通过 GitHub GraphQL API 扫描仓库的 295 个 merged PR，并检查 closing Issue、PR/commit 文本中的 `fixes`、`closes`、`resolves` 引用以及测试文件变更：

- 满足 SWE-bench “merged + resolved Issue” 严格条件的 PR：0。
- 包含测试文件变更的 merged PR：3，分别为 PR #78、#242、#21。
- 这 3 个 PR 均未关联独立 Issue，不能直接通过 `is_valid_pull`。

本次选择 PR #349 `修复错误颜色值导致的UI显示问题` 进行兼容性试验。该 PR 只修改 `entry/src/main/ets/pages/view/Reader/ReaderPage3.ets`，将 3 个错误的主题颜色值替换为正确配置，补丁规模较小且问题边界明确。

### 依赖与加载方式

采集代码依赖 `ghapi`、`beautifulsoup4`、`requests` 和 `unidiff`。仓库内代码使用同步 GhApi，因此需要 `ghapi 1.x`：

```powershell
python -m pip install "ghapi<2" beautifulsoup4 unidiff
```

`ghapi 2.x` 的 API 调用返回 coroutine，与当前 `Repo.call_api` 不兼容。顶层 `import swebench` 还会导入 Docker harness 等与采集无关的依赖；本次测试通过隔离加载 `swebench.collect` 子包，只复用以下实现：

- `swebench.collect.print_pulls.log_single_pull`
- `swebench.collect.build_dataset.main`
- `swebench.collect.utils.extract_patches`

### 原始 SWE-bench 采集结果

`log_single_pull` 生成原始 PR 元数据：

```text
instances/issue_resolve/legado-harmony-pr-349/collection/pr-349.jsonl
```

关键字段：

```text
pull_number: 349
base_commit: 1c824edaa798635dceeac4de299093e707316bdd
head_commit: 248d31eeaa01ddc121b3ef845a195b4df79a9549
merge_commit: d437c9a03fe9668d47aa356c10f4d69c5f4a2291
resolved_issues: []
```

将该文件直接输入 `build_dataset.main` 后，生成以下两个空文件：

```text
instances/issue_resolve/legado-harmony-pr-349/collection/swebench-candidate.jsonl
instances/issue_resolve/legado-harmony-pr-349/collection/swebench-candidate.jsonl.all
```

这是预期结果：`is_valid_pull` 要求 `resolved_issues` 至少包含一个 Issue，而该仓库的 PR 没有使用 SWE-bench 支持的 closing Issue 约定。

### 兼容性降级

为了验证 HarmonyOS PR 到 instance 的其余流程，本次采用以下显式降级，不伪造 Issue 编号：

- `issue_numbers` 保持空列表。
- 使用 PR 标题扩展为 `problem_statement`。
- 继续使用 SWE-bench `extract_patches` 从 PR diff 拆分 `patch` 和 `test_patch`。
- 原 PR 没有测试文件变更，因此 `test_patch` 为空。
- 增加静态验证脚本检查目标颜色配置。

最终 instance 保留 SWE-bench 的核心字段：`repo`、`pull_number`、`base_commit`、`patch`、`test_patch`、`problem_statement`、`hints_text` 和 `created_at`，同时增加 commit、artifact、兼容性说明和文件哈希。

### 基线快照

由于当前环境执行 `git clone` 时 GitHub Git 连接超时，改用 GitHub tarball API 下载 PR base commit，并解压到：

```text
test_project/legado-Harmony
```

该目录对应 upstream base commit `1c824edaa798635dceeac4de299093e707316bdd`。为方便本地 `git apply` 和恢复检查，解压后创建了本地快照 commit `3b0fc437887d0132cb20008567ee75b7606e62a6`；该本地 commit 只表示同一文件快照，不替代 upstream commit 标识。

### Instance 文件

```text
instances/issue_resolve/legado-harmony-pr-349/
├── collection/
│   ├── pr-349.jsonl
│   ├── swebench-candidate.jsonl
│   ├── swebench-candidate.jsonl.all
│   └── syntax-tree-summary.json
├── fix.patch
├── instance.json
├── task.md
├── test.patch
├── validation.json
└── verify.py
```

- `fix.patch`：SWE-bench 从 PR diff 提取的 gold patch。
- `test.patch`：原 PR 没有测试变更，因此为空文件。
- `instance.json`：兼容 SWE-bench 核心字段的 instance 元数据。
- `verify.py`：检查 3 个修复后颜色值，并确保旧颜色值已移除。
- `validation.json`：记录补丁、哈希、语法树和环境验证结果。

### 验证过程

在未应用补丁的 base snapshot 上，`verify.py` 会失败；随后执行：

```powershell
git -C test_project/legado-Harmony apply --check `
  ../../instances/issue_resolve/legado-harmony-pr-349/fix.patch

git -C test_project/legado-Harmony apply `
  ../../instances/issue_resolve/legado-harmony-pr-349/fix.patch

python instances/issue_resolve/legado-harmony-pr-349/verify.py `
  test_project/legado-Harmony

PYTHONPATH=src python tools/parse_arkts_syntax_tree.py `
  test_project/legado-Harmony `
  --output /tmp/legado-pr-349-syntax.jsonl `
  --summary instances/issue_resolve/legado-harmony-pr-349/collection/syntax-tree-summary.json

git -C test_project/legado-Harmony apply --reverse `
  ../../instances/issue_resolve/legado-harmony-pr-349/fix.patch
```

验证结果：

```text
gold patch apply check: passed
fixed static verifier: passed
ArkTS/TS parsed files: 346
syntax tree imports: 1274
syntax tree nodes: 10932
snapshot reverse apply and clean check: passed
```

目标文件应用补丁前后的 SHA-256：

```text
original: 38003b64bec4d15ea9a441c9d38b83d71a7ad314ba282f9ca9cf46c01853783c
fixed:    8084dba6e24a7959926eeee76a85efd30b34b5a62aa1876d10c118e2c0d561f9
```

当前环境没有 DevEco Studio、`ohpm` 或项目内可执行的 hvigor wrapper，且原 PR 没有测试补丁，因此本次没有执行原生 HarmonyOS 构建或设备测试。该实例适合作为 PR 采集和静态补丁验证流程的样例，不应被标记为严格满足原始 SWE-bench Issue+测试准入条件的评测实例。

### 试验结论

- 已成功复用 `swebench.collect` 完成 PR #349 元数据采集和 gold patch 提取。
- 已在 `test_project/legado-Harmony` 恢复 upstream base commit 对应的 346 文件 ArkTS/TS 快照。
- 已生成 `instances/issue_resolve/legado-harmony-pr-349/instance.json`、任务描述、补丁、验证器、采集过程文件和验证结果。
- Gold patch 正向应用、修复后静态验证、ArkTS 结构解析、反向恢复和工作区清洁检查均通过。
- 原始 `build_dataset` 拒绝该 PR 是正确行为，因为 `resolved_issues=[]`；兼容实例没有伪造 Issue 编号，而是明确记录了使用 PR 标题作为问题描述的降级方式。
- `mgz0227/legado-Harmony` 的 295 个 merged PR 中，没有 PR 满足 SWE-bench 的 “merged + closing Issue” 严格条件；只有 3 个 merged PR 包含测试文件变更，且它们同样没有关联独立 Issue。
- 因此，该实例只能作为 HarmonyOS PR 采集、补丁构造和静态验证流程样例，不能纳入要求真实 Issue 关联和测试补丁的严格评测集。

## 开发与测试

```powershell
python -m unittest discover -s tests
```
