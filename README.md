# HarmonyOS Code Benchmark 构造工具

本仓库面向 HarmonyOS 开源项目构造 code benchmark。整体流程先从 GitHub 搜索、过滤并持久化候选仓库，再基于真实仓库快照生成可复现、可验证的代码任务 instance。

## 1. GitHub Repo Filter

GitHub Repo Filter 按关键字、仓库名、描述、README、主要语言、topic、owner/org 等条件搜索 GitHub 仓库，再用本地配置过滤创建时间、最近更新时间、stars、forks、license 等字段，最后把命中的仓库元数据以 JSONL 持久化到本地。该阶段只保存元数据，不 clone 仓库。

### 1.1 快速开始
```powershell
$env:GITHUB_TOKEN="ghp_xxx"
python run.py --config config.json
```

也可以使用配置文件：

```powershell
python run.py --config config.example.json
```

未设置 `GITHUB_TOKEN` 时也能访问公开搜索接口，但 GitHub 未认证请求的 rate limit 更低。

### 1.2 后处理筛选

拉取完成后可以对 `data/repositories.jsonl` 继续做顺序筛选：

```powershell
python filter_repositories.py data/repositories.jsonl
```

默认筛选步骤：

1. stars > 10
2. updated_at >= 2026-01-01

筛选结果：

```text
repositories_harmony.jsonl: 64180
repositories_harmony_stars_gt10.jsonl: 1502
repositories_harmony_stars_gt10_language_typescript.jsonl: 253
```

3. HarmonyOS/ArkTS 项目结构筛选
- ArkTS 源码：仓库文件树中存在 `.ets` 文件。
- HarmonyOS 构建标记：`build-profile.json5`、`hvigorfile.ts`、`oh-package.json5`、`AppScope/app.json5`、`module.json5`、`hvigor-config.json5`。
- 辅助元数据关键词：`ArkTS`、`ArkUI`、`HarmonyOS`、`OpenHarmony`、`OHOS`、`鸿蒙`。单独出现泛化关键词 `Harmony` 不作为 HarmonyOS 证据。
- 置信等级规则:
  - `high`：存在 `.ets` 文件，并且命中至少两个 HarmonyOS 构建标记
  - `medium`：存在 `.ets`、至少一个构建标记和明确的 HarmonyOS 元数据关键词；或者命中至少三个构建标记和明确的元数据关键词

2026-08-03 的筛选结果：
```text
repositories_harmony_stars_gt10_language_typescript.jsonl: 253
repositories_harmony_stars_gt10_language_typescript_harmonyos_arkts.jsonl: 122
high confidence: 121
medium confidence: 1
GitHub tree inspection error: 1
```

4. GPT 仓库语义复核

复核同时检查 GitHub 仓库描述、README 和根目录，采用以下判定规则：

- 仓库主体是 HarmonyOS 应用、组件库、SDK、开发工具或迁移工具时保留
- 跨平台或 monorepo 仓库只要包含 README 明确说明且实际维护的 HarmonyOS 客户端、SDK 或子项目，也视为与 HarmonyOS 相关并保留
- 仅出现泛化的 `Harmony` 关键词、偶然的 ArkTS 文件、生成产物或不构成实际 HarmonyOS 项目的示例时剔除

2026-08-03 的语义复核结果：

```text
输入仓库：34
保留仓库：34
剔除仓库：0
```

## 2. Instance 生成

按照 instance 类型分类，最终选择 "2.5 函数 mask + LLM 生成测试文件 harbor instance 构造" 方案

### 2.1 ArkTS AST 错误实例构造

#### ArkTS 语法树解析

工作区提供一个通用 HarmonyOS ArkTS/ETS 结构解析脚本，不需要写入被解析的目标仓库：

```powershell
git clone https://github.com/ausboyue/Wechat_HarmonyOS test_project/Wechat_HarmonyOS
python tools/parse_arkts_syntax_tree.py test_project/Wechat_HarmonyOS
```

默认扫描 `.ets` 和 `.ts` 文件，跳过 `.git`、`build`、`node_modules`、`oh_modules` 等目录，输出到工作区：

- `syntax_trees/<repo-name>_syntax_tree.jsonl`
- `syntax_trees/<repo-name>_syntax_tree_summary.json`

#### 错误实例构造

项目可以基于已提取的 ArkTS 抽象语法树，自动筛选高调用出度且被多个跨文件函数调用的目标函数。生成器会枚举条件取反、比较符替换、逻辑符替换、数值边界、赋值运算符替换和独立副作用调用删除六类单点 mutation

详细内容见 [ArkTS AST 错误实例构造文档](src/arkts_syntax_tree/README.md)。

### 2.2 接口/基类实现补全实例构造

生成器会发现 `interface` 或拥有派生实现的基类，并要求至少存在两个实现/使用文件；其中支持显式的 `implements`/`extends`，接口还支持 ArkTS 常见的结构化使用。生成时选择其中一个实现文件进行整文件 mask，要求智能体参考抽象定义和其他实现文件，在原路径补全被遮蔽的实现

```powershell
  src python tools/build_feature_instance.py test_project/Wechat_HarmonyOS
```

### 2.3 应用迁移实例构造（未实现）

目标是构建从 android.* 实现方式到 arkts 实现方式的实例，检索结果发现 arkts 项目仓库不存在 android.* 调用:

对 `repositories_harmony_stars_gt10_language_typescript.jsonl` 执行批量扫描。该文件实际包含 253 条仓库记录，最终结果为：

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

### 2.4 仓库 Issue 解决实例构造（未实现）

面向公开仓库中的真实 Issue 和对应 Pull Request 构造问题修复任务。计划关联 Issue 描述、PR 的 base commit、gold patch、测试变更和验证条件，使生成的 instance 能够复现并检查 Issue 是否被正确解决

检索发现 git repository PR 质量较低，不适用于本方案

#### 过滤后仓库的 PR 分布

2026-08-03 使用 GitHub GraphQL API 对以下最新过滤结果进行统计：

```text
data/repositories_harmony_stars_gt10_language_typescript_harmonyos_arkts.jsonl
```

该文件包含 122 个 high/medium 置信的 HarmonyOS/ArkTS 仓库，全部能够正常查询 PR。PR 状态分布如下：

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

这些 merged PR 只是初始候选，不等同于可直接使用的 benchmark instance。按照 SWE-bench 的筛选逻辑，PR 还需要关联至少一个被解决的 Issue，并能提取非空的问题描述和 gold patch；用于评测的数据还需要包含测试代码变更

### 2.5 函数 mask + LLM 生成测试文件 harbor instance 构造 (正在使用)

#### 入口函数
tools/build_arkts_bug_harbor_instance.py 
从一个真实 ArkTS/HarmonyOS Git 仓库中构造两个函数恢复任务。脚本先在临时 checkout 中生成或读取语法树，结合函数调用图筛选高影响候选函数，再将候选函数的函数体替换为 mask。每个候选函数都会生成一个独立的 Harbor task

#### 构造流程

1. **准备仓库快照**：`--repo` 支持 GitHub `owner/name`、Git URL 和本地 Git 仓库。仓库会复制或克隆到 `--checkout-root` 下的临时目录，原始目录不会被修改；记录 checkout 的 `HEAD` commit，作为 Harbor 环境中的固定版本
2. **生成候选函数**：未指定 `--syntax-tree` 时，脚本在 checkout 根目录生成 `<repo-name>_syntax_tree.jsonl` 及汇总 JSON。候选分析复用 `build_arkts_bug_mask_instance.py`，默认要求函数出度至少为 1、下游依赖数至少为 1，并按影响面排序，取前两个候选函数
3. **生成错误和标准答案补丁**：脚本恢复到原始 commit 后逐个处理候选函数，分别保存从原实现到 mask 的 `error.patch` 和从 mask 到原实现的 `solution/gold.patch`
4. **生成并验证测试**：默认调用 Codex（可通过 `--codex-cli`、`--model` 和 `--codex-sandbox` 配置）只创建 `tests/test_outputs.py`。脚本检查 Codex 是否只修改该文件，先确认测试在 mask 代码上失败，再应用 gold patch 确认恢复后通过。测试必须执行目标函数并检查可观察行为，不能只检查函数体非空或复制标准答案。
5. **写出 Harbor task**：每个实例包含 `instruction.md`、`instance.json`、`error.patch`、`tests/`、`solution/` 和 `environment/`。环境使用 Node.js Docker 镜像，安装 Python/pytest、Node.js 和 Codex；`tests/test.sh` 应用测试补丁并运行 `pytest`，`solution/solve.sh` 应用标准答案补丁

#### 单仓库用法

```bash
# 默认生成两个实例，输出到 harbor_instances/
python tools/build_arkts_bug_harbor_instance.py --repo owner/repository

# 指定模型并保留临时 checkout，便于检查语法树和中间文件
python tools/build_arkts_bug_harbor_instance.py \
  --repo owner/repository \
  --model gpt-5.6-sol \
  --keep-checkout

# 只查看按当前筛选条件排序的候选函数，不创建实例
python tools/build_arkts_bug_harbor_instance.py \
  --repo owner/repository \
  --list-candidates
```

主要参数如下：

- `--output-dir`：实例输出根目录，默认 `harbor_instances/`。
- `--checkout-root`：临时 checkout 根目录，默认 `.tmp/arkts-harbor-checkouts/`。
- `--syntax-tree`：复用已有语法树 JSONL；必须与当前 checkout 版本匹配。
- `--instance-id`：实例 ID 前缀，最终生成 `<prefix>_0` 和 `<prefix>_1`。
- `--min-out-degree`、`--min-consumers`、`--min-downstream-dependencies`：候选函数筛选阈值。
- `--mutation-operator`：限定候选使用的变异算子，如 `conditional_negation`、`comparison_replacement` 或 `call_deletion`。
- `--skip-codex`：跳过 LLM 测试生成，仅对 `entry/src/ohosTest/ets/test/Ability.test.ets` 提供内置 Hypium/Node VM 行为测试回退；其他函数会直接报错，不会生成不充分的测试。

#### 数据集批量构造

对仓库 JSONL 清单可以使用批处理包装脚本。它默认处理前 20 条记录，检测到输出目录中已有同仓库 instance 时跳过，单个仓库失败后继续处理后续记录：

```bash
python tools/build_arkts_bug_harbor_batch.py \
  data/repositories_harmony_stars_gt10_language_typescript_harmonyos_arkts_PR_merged_gpt_filter.jsonl \
  --output-dir harbor_instances \
  --model gpt-5.6-sol \
  --max-repos 20
```

#### Harbor 评测指令

对单个 Harbor instance 启动评测，采用下面的指令：

```bash
harbor run \
  --path harbor_instances/<instance-id> \
  --agent codex \
  --model gpt-5.6-sol
```

评测结果写入 `jobs/`（或通过 `--jobs-dir` 指定的目录）

对整个 `harbor_instances/` 数据集跑评测时，推荐使用 job 配置文件而不是逐个 instance 执行：

```json
{
  "job_name": "your defined job dir name, timestamp default",
  "jobs_dir": "/absolute/path/to/jobs/<model-name>",
  "retry": { "max_retries": 2 },
  "agents": [
    {
      "name": "mini-swe-agent",
      "model_name": "openai/${model}",
      "env": {
        "OPENAI_API_KEY": "${API_KEY}",
        "OPENAI_BASE_URL": "${BASE_URL}",
        "CURL_IPRESOLVE": "4"
      }
    }
  ],
  "datasets": [
    { "path": "/absolute/path/to/harbor_instances" }
  ]
}
```

```bash
export API_KEY=<your-key>
harbor run -c path/to/job_config.json
```

#### 失败实例重跑（resume）

重跑不需要另起 job，直接用 `harbor job resume` 原地续跑：

```bash
export API_KEY=<your-key>
harbor job resume -p jobs/<model>/<timestamp>
```

**注意**：job 目录由 `jobs_dir / job_name` 决定，如果采用 config 模式进行重跑，需要在 resume 前往 job 的 `config.json` 里补一行：

```json
"job_name": "2026-09-20__17-56-59"   // 与原目录名一致
```

## 3. trajectory rubric
详细内容见 [trajectory rubric 蒸馏文档](src/trajectory_analyse/README.md)