# ArkTS Code Benchmark 实例构造

本模块面向 HarmonyOS ArkTS/ETS 项目，提供轻量抽象语法树提取，并基于真实仓库构造两类 code benchmark instance：

| 类型 | 目标 | 默认输出目录 | 基线形式 | 验收方式 |
| --- | --- | --- | --- | --- |
| ArkTS AST 错误实例构造 | 注入可定位、可恢复的返回值错误，形成修复任务 | `instances/error_fix/` | `bug.patch` 与 `fix.patch` | 补丁、构建和测试 |
| 接口/基类实现补全实例构造 | mask 一个已有实现文件，要求智能体在原路径补全 | `instances/feature_implement/` | `mask.patch` 与 `gold.patch` | 静态结构验收与 gold patch |

两类任务共享 `parser.py` 提取的 ArkTS/ETS 结构信息，且都不保存仓库副本。错误实例通过 `bug.patch`/`fix.patch` 表达错误注入和修复；实现补全实例通过 `mask.patch`/`gold.patch` 表达实现遮蔽和参考答案。

## 1. ArkTS AST 错误实例构造

### 1.1 模块组成

- `parser.py`：扫描 `.ets`、`.ts` 文件，提取 imports、函数和容器节点、源码范围、修饰符及基础 metrics。
- `bug_instance.py`：构建候选函数集合，分析调用出度和跨文件返回值消费者，执行 mutation，并生成快照、补丁和元数据。
- `../../tools/parse_arkts_syntax_tree.py`：生成语法树 JSONL 和汇总 JSON 的命令行入口。
- `../../tools/build_arkts_bug_instance.py`：查看候选或生成错误 instance 的命令行入口。
- `../../tests/test_bug_instance.py`：候选识别、错误注入、换行保留和补丁方向的自动化测试。

### 1.2 工作流程

#### 1. 提取语法树

先对目标仓库执行结构解析：

```bash
python3 tools/parse_arkts_syntax_tree.py test_project/Wechat_HarmonyOS
```

默认输出：

```text
syntax_trees/Wechat_HarmonyOS_syntax_tree.jsonl
syntax_trees/Wechat_HarmonyOS_syntax_tree_summary.json
```

JSONL 中每条记录对应一个源码文件，包含：

- 源码相对路径和语言；
- import source、clause 和行号；
- class、struct、interface、function、method、callback 等节点；
- 节点的起止行、signature、decorators 和 modifiers；
- 节点数量、深度和类型统计。

#### 2. 构建候选函数

`find_bug_candidates()` 展开语法树中的函数节点，并筛选同时满足以下条件的普通函数或方法：

1. 不是匿名函数或 callback；
2. 存在可以自动替换的返回语句；
3. 唯一调用出度达到 `min_out_degree`，默认值为 3；
4. 返回值至少被 `min_downstream_consumers` 个跨文件下游函数消费，默认值为 2。

调用出度是在函数源码范围内提取的唯一调用目标数量。例如：

```ts
const manager = createManager()
const result = manager.request()
return result.every(checkItem)
```

可得到 `createManager`、`manager.request` 和 `result.every` 等调用目标。重复调用同一目标只计算一次。

#### 3. 识别跨文件消费者

分析器利用 AST JSONL 中的 import 信息解析相对导入，并为 named、default、namespace 和别名导入建立调用匹配规则。调用点必须位于候选函数所在文件之外。

当前认为以下情况实际消费了返回值：

- 赋值给变量，而且该变量在所属函数的后续代码中被使用；
- 直接用于 `if`、`while`、`for` 等条件；
- 直接作为当前函数的返回值；
- 作为另一个函数调用的参数；
- 进入 Promise `.then()` 链。

裸调用以及“赋值后从未使用”的调用不会计入下游消费者。多个调用点落在同一个下游函数时，只计为一个消费者。

#### 4. 选择并注入 mutation

分析器从候选函数末尾向前查找可变异的 `return`，然后根据返回类型和原表达式推断错误值：

| 返回类型或表达式 | 注入值 |
| --- | --- |
| `boolean`、`Promise<boolean>` 或布尔表达式 | `false`，原值为 `false` 时使用 `true` |
| `string` | `""` |
| `number` | `0` |
| 数组 | `[]` |
| 其他类型 | `null` |

候选函数按下游消费者数量、唯一调用出度、文件路径和源码行号排序。默认选择排名最高的候选，只替换目标返回语句所在的一行，并保留原文件的 LF 或 CRLF 换行格式。

#### 5. 生成 instance

`create_bug_instance()` 直接根据目标文件的原始内容和 mutation 内容生成双向补丁，不复制仓库，也不写入注入错误后的源码快照。输入的语法树 JSONL 会复制为 instance 目录中的 `syntax_tree.jsonl`，与 `instance.json` 同级保存。

输出目录必须位于源仓库之外，且不能与已有 instance 重名。默认结构如下：

```text
instances/error_fix/<instance-id>/
├── bug.patch      # 原始代码 -> 错误代码
├── fix.patch      # 错误代码 -> 原始代码
├── syntax_tree.jsonl
└── instance.json  # instance 元数据和任务描述
```

`instance.json` 包含：

- instance ID 和创建时间；
- 原仓库规范化后的 GitHub 链接和 Git commit，不记录原仓库绝对路径；
- 目标函数、源码范围、signature 和 modifiers；
- 唯一调用出度和 callee 列表；
- 跨文件消费函数、调用位置和消费类型；
- mutation 前后的表达式与源码行；
- 原文件和错误文件的 SHA-256；
- 使用“以下函数{functions}调用失败，找出错误”模板生成的任务 `description`。

### 1.3 命令行使用

只查看所有匹配候选，不生成 instance：

```bash
python3 tools/build_arkts_bug_instance.py \
  test_project/Wechat_HarmonyOS \
  --syntax-tree syntax_trees/Wechat_HarmonyOS_syntax_tree.jsonl \
  --list-candidates
```

生成错误 instance：

```bash
python3 tools/build_arkts_bug_instance.py \
  test_project/Wechat_HarmonyOS \
  --syntax-tree syntax_trees/Wechat_HarmonyOS_syntax_tree.jsonl \
  --instance-id wechat-permission-request-return-false
```

主要参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--syntax-tree` | `syntax_trees/<repo-name>_syntax_tree.jsonl` | AST JSONL 路径 |
| `--output-dir` | `instances/error_fix` | instance 父目录 |
| `--min-out-degree` | `3` | 最小唯一调用出度 |
| `--min-consumers` | `2` | 最小跨文件下游消费者数量 |
| `--instance-id` | 自动生成 | instance 目录名 |
| `--list-candidates` | 关闭 | 只输出候选 JSON，不生成 instance |

在 Windows 工作区中可通过 WSL 执行同一命令：

```powershell
wsl bash -lc "python3 tools/build_arkts_bug_instance.py test_project/Wechat_HarmonyOS --syntax-tree syntax_trees/Wechat_HarmonyOS_syntax_tree.jsonl --list-candidates"
```

### 1.4 Wechat 示例

当前 `Wechat_HarmonyOS` 语法树会识别：

```text
目标函数       PermissionUtils.request
唯一调用出度   5
下游消费者     3
mutation       return isAuth -> return false
```

下游消费者包括：

- `ChatPage.build.anonymous@361`；
- `PhotoPickerUtils.openGallery`；
- `PhotoPickerUtils.openCamera`。

已生成实例位于：

```text
instances/error_fix/wechat-permission-request-return-false/
```

### 1.5 验证

运行测试：

```bash
python3 -m unittest tests.test_bug_instance
```

可以先将 `bug.patch` 应用到独立的仓库副本，再检查 `fix.patch` 是否可以恢复：

```bash
cd <独立仓库副本>
patch --dry-run -p1 -i ../instances/error_fix/<instance-id>/bug.patch
patch -p1 -i ../instances/error_fix/<instance-id>/bug.patch
patch --dry-run -p1 -i ../instances/error_fix/<instance-id>/fix.patch
```

`--dry-run` 只验证补丁，不会修改仓库副本。

### 1.6 当前边界

当前实现是轻量 AST 与源码静态分析工具，不是完整 ArkTS 编译器或全程序数据流引擎：

- 主要解析相对 import，不解析工程路径别名、动态 import 或运行时注入；
- 调用和返回值消费使用源码模式匹配，复杂跨行表达式可能无法识别；
- 不处理反射、动态属性访问和运行时多态调用关系；
- mutation 基于返回类型和表达式启发式推断，不保证每个错误都能通过编译；
- instance 生成阶段不会自动执行目标 HarmonyOS 项目的构建或测试。

因此，生成 instance 后仍应结合 `fix.patch` dry-run、项目构建和测试结果进行最终确认。

## 2. 接口/基类实现补全实例构造

该任务从真实仓库中选择一个已有多个实现或使用文件的 `interface`/`class`，为其中一个实现文件生成整文件 mask patch，要求智能体在独立仓库副本上应用 patch 后，参考抽象定义和其他实现文件补全实现。instance 不保存仓库；原始实现以 `gold.patch` 的形式作为 benchmark gold label。

### 2.1 模块组成

- `feature_instance.py`：发现抽象节点及其实现/使用文件，选择 mask 目标，生成双向 patch、语法树、任务描述和验收元数据。
- `parser.py`：直接解析目标仓库，或为外部传入的语法树 JSONL 提供结构数据。
- `../../tools/build_feature_instance.py`：列出候选节点或生成实现补全 instance。
- `../../tools/verify_feature_instance.py`：验证被 mask 的文件是否已补全并恢复实现关系。
- `../../tests/test_feature_instance.py`：覆盖候选发现、mask、gold patch、等价实现和精确 gold 匹配。

### 2.2 候选抽象节点

`find_feature_candidates()` 从语法树中收集 `interface` 和 `class` 节点，再查找位于其他源码文件中的真实实现或派生关系。默认只保留至少关联两个 `implements`/`extends` 文件的节点。

支持两类关系，其中默认只启用第一类：

1. **显式继承或实现**：源码中的 `class` 或 `struct` 通过 `implements` 或 `extends` 使用目标抽象节点；
2. **接口结构化使用**：文件从目标接口的定义文件导入该接口，并在 import 之外的实际代码中再次使用该类型；该模式需要显式传入 `include_structural_usage=True` 或 CLI 参数 `--include-structural-usage`，不再作为 `feature_implement` 默认候选。

候选关联遵循以下规则：

- 解析相对 import，确认导入来源确实指向抽象节点所在文件，避免把其他模块中的同名类型误判为实现；
- 支持 named import、named import alias 和 default import；
- 结构化使用只适用于 `interface`，普通基类必须通过 `extends` 建立关系；
- 类型名称只在有效代码中计数，注释和字符串中的名称不会形成结构化使用；
- 抽象节点定义文件自身不计入实现文件数量；
- 候选按实现文件数量降序排列，再按定义路径和源码行号排序，默认选择排名第一的候选。

例如，Wechat 示例中的 `ChatContentItemData` 接口会关联以下三个结构化使用文件：

```text
entry/src/main/ets/component/ListChatContentLeftItem.ets
entry/src/main/ets/component/ListChatContentRightItem.ets
entry/src/main/ets/pages/chat/ChatPage.ets
```

### 2.3 列出候选

直接解析仓库并列出所有候选：

```bash
python3 tools/build_feature_instance.py \
  test_project/Wechat_HarmonyOS \
  --list
```

也可以复用已经生成的语法树 JSONL：

```bash
python3 tools/build_feature_instance.py \
  test_project/Wechat_HarmonyOS \
  --syntax-tree syntax_trees/Wechat_HarmonyOS_syntax_tree.jsonl \
  --min-implementations 2 \
  --list
```

候选 JSON 包含：

- 抽象节点的路径、类型、名称、源码范围、signature 和 modifiers；
- 实现/使用文件数量；
- 每个文件的关系类型、导入后的本地名称以及文件内的 `class`/`struct` 声明。

### 2.4 Mask 目标选择

候选确定后，生成器从该候选的实现/使用文件中选择一个文件进行 mask。排序规则依次为：

1. 优先选择包含 `class` 或 `struct` 声明的文件；
2. 优先选择显式 `implements`/`extends` 文件；
3. 最后按文件路径排序，保证结果可复现。

Mask 采用整文件遮蔽。`mask.patch` 会把目标文件替换为仅包含目标抽象节点和预期声明名称的注释占位符，例如：

```ts
// CODE BENCHMARK MASK
// Restore the implementation for: ChatContentItemData
// Expected declarations: ListChatContentLeftItem
```

原文件内容保持在源仓库中，不会被修改。生成器同时创建两个统一 diff：

- `mask.patch`：原文件内容 → mask 内容，用于构造待解决基线；
- `gold.patch`：mask 内容 → 原文件内容，作为 gold label。

### 2.5 生成 instance

使用默认输出目录生成 instance：

```bash
python3 tools/build_feature_instance.py test_project/Wechat_HarmonyOS
```

默认生成结构：

```text
instances/feature_implement/<instance-id>/
├── mask.patch        # 原实现 -> mask 内容
├── gold.patch        # mask 内容 -> 原实现的 gold label
├── syntax_tree.jsonl # 构造时使用的 ArkTS 结构信息
└── instance.json     # 候选、任务描述、mask 和验收元数据
```

该格式与 2.1 错误实例一致，通过补丁表达代码变化，不复制或修改原始仓库。评测环境需要根据 `source_repo.commit` 准备独立仓库副本，再应用 `mask.patch` 构造任务基线。

输出目录必须位于源仓库之外，且目标 instance 目录不能已经存在。默认 instance ID 使用 `<仓库名>-<随机 UUID>`，例如 `Wechat_HarmonyOS-550e8400-e29b-41d4-a716-446655440000`。自定义 `instance-id` 仍只能包含字母、数字、点、下划线和连字符。

主要参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--syntax-tree` | 不指定 | 语法树 JSONL；省略时直接解析仓库 |
| `--output` / `--output-dir` | `instances/feature_implement` | instance 父目录 |
| `--instance-id` | `<仓库名>-<随机 UUID>` | instance 目录名 |
| `--min-implementations` | `2` | 候选至少关联的实现/使用文件数 |
| `--include-structural-usage` | 关闭 | 允许把仅导入并使用 interface 类型的文件作为候选 |
| `--list` | 关闭 | 只输出候选 JSON，不生成 instance |

### 2.6 Instance 元数据

`instance.json` 的核心字段包括：

| 字段 | 内容 |
| --- | --- |
| `task_type` | 固定为 `feature_implementation` |
| `source_repo` | 原仓库名称、规范化 GitHub URL 和 Git commit |
| `target.abstract_node` | 目标接口或基类信息 |
| `target.implementation_files` | 基线实现/使用文件及关系类型 |
| `mask` | mask 文件、关系类型、预期声明以及 mask 前后 SHA-256 |
| `description` | 面向智能体的实现补全任务描述 |
| `reference_implementation_files` | 未被 mask、可供参考的其他实现文件 |
| `patches` | `mask.patch` 和 `gold.patch` 路径 |
| `gold_label` | `gold.patch` 的类型、路径和应用根目录 |
| `affected_modules` | 抽象定义、mask 文件和参考实现文件 |
| `acceptance.checks` | 静态结构验收条件 |

`description` 会明确说明目标抽象节点、mask 文件和现有参考文件。任务要求包括：

- 保留目标抽象节点；
- 在原路径补全被 mask 的文件；
- 恢复原文件中的 `class`/`struct` 声明；
- 恢复目标接口或基类的实现/使用关系；
- 不修改其他现有实现的对外行为。

### 2.7 Gold patch

`gold.patch` 是从 mask 后内容恢复到原文件内容的统一 diff：

```text
--- a/<masked-path>
+++ b/<masked-path>
@@ ...
-// CODE BENCHMARK MASK
+<original implementation>
```

两个 patch 的应用根目录都是目标仓库副本。`gold.patch` 是参考答案，不要求智能体提交内容与原文件逐字相同；验证器允许结构上等价的实现通过，同时通过 `matches_gold` 字段报告当前文件是否与原实现的 SHA-256 完全一致。

### 2.8 静态结构验收

准备任务基线：

```bash
cd <独立仓库副本>
patch --dry-run -p1 -i <instance-dir>/mask.patch
patch -p1 -i <instance-dir>/mask.patch
```

智能体在该仓库副本中完成实现后，可以运行：

```bash
python3 tools/verify_feature_instance.py \
  instances/feature_implement/<instance-id> \
  <独立仓库副本>
```

`verify_feature_instance()` 接收 instance 目录和外部工作仓库，重新解析工作仓库并分别检查：

1. `abstract_node_exists`：原接口或基类仍然存在，路径、节点类型和名称保持一致；
2. `masked_file_exists`：被 mask 的原路径仍然存在；
3. `masked_file_changed`：文件内容不再是生成器写入的 mask 占位符；
4. `implementation_relation_restored`：该文件重新形成目标抽象节点的实现或结构化使用关系；
5. `expected_declarations_restored`：原文件中的 `class`/`struct` 声明已经恢复。

所有检查通过时返回 `passed: true`，CLI 退出码为 0；否则返回 `passed: false`，CLI 退出码为 1。刚生成的 instance 验证失败是预期行为，因为目标文件仍然是 mask 内容。`matches_gold: true` 表示文件与原实现完全一致；等价实现可以在 `matches_gold: false` 时通过静态验收。

生成后的示例目录名形如：

```text
instances/feature_implement/Wechat_HarmonyOS-<uuid>/
```

### 2.9 Python API

```python
from arkts_syntax_tree import (
    create_feature_instance,
    find_feature_candidates,
    verify_feature_instance,
)

candidates = find_feature_candidates(
    "test_project/Wechat_HarmonyOS",
    min_implementation_files=2,
    include_structural_usage=True,
)

metadata = create_feature_instance(
    "test_project/Wechat_HarmonyOS",
    output_dir="instances/feature_implement",
)

result = verify_feature_instance(
    "instances/feature_implement/Wechat_HarmonyOS-<uuid>",
    "/tmp/Wechat_HarmonyOS-working",
)
```

### 2.10 验证测试

运行实现补全实例专项测试：

```bash
PYTHONPATH=src python3 -m unittest tests.test_feature_instance -v
```

运行全部测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

### 2.11 当前边界

- 当前只扫描 `.ets` 和 `.ts` 文件；
- import 解析以相对路径为主，不解析工程路径别名、动态 import 或跨包 re-export；
- 显式关系通过源码结构模式识别，不等价于完整 TypeScript/ArkTS 类型检查；
- 接口结构化使用以“正确导入并在有效代码中再次引用”为判断依据，不能证明对象完整实现了接口中的所有成员；
- mask 当前采用整文件替换，不在 mask 后基线中保留原文件的 imports、局部实现或注释；
- 静态验收确认声明和结构关系，不检查业务语义、UI 效果、运行时行为或性能；
- 生成器不会自动运行目标 HarmonyOS 工程的构建和测试，最终 instance 仍应结合项目自身验证流程进行人工确认。

### 2.12 Oracle 生成

`oracle_generator.py` 负责从原始仓库的抽象定义文件自动提取 oracle，并为 `feature_implement` instance 生成可执行断言。

```bash
python3 tools/build_feature_oracle.py \
  instances/feature_implement/<instance-id> \
  test_project/legado-Harmony
```

默认输出到 `instances/feature_implement/<instance-id>/oracle/`，其中包含：

- `test_plan.json`：从原始源码抽取出的 oracle 计划，记录接口字段、枚举成员、类型别名和值映射；
- `Oracle.test.ets`：根据 `test_plan.json` 生成的 Hypium 测试文件。

当前 oracle 生成策略是“原始仓库取值 + 自动生成断言”：

- 默认优先使用 `target.abstract_node.path` 作为 oracle 来源；若该文件不可用，再尝试 `mask.path`；
- 接口字段会被解析成一个带类型标注的样例对象；
- 枚举会生成稳定值断言；
- `Record` 常量会生成键值映射断言；
- 纯类型别名会在样例值可推导时生成编译期和运行期断言；
- 无法自动推导样例值的导出会直接报错，避免生成弱 oracle。
