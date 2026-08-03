# ArkTS Code Benchmark 实例构造

本模块面向 HarmonyOS ArkTS/ETS 项目，提供轻量抽象语法树提取，并基于真实仓库构造两类 code benchmark instance：

| 类型 | 目标 | 默认输出目录 | 基线形式 | 验收方式 |
| --- | --- | --- | --- | --- |
| ArkTS AST 错误实例构造 | 注入可定位、可恢复的返回值错误，形成修复任务 | `instances/error_fix/` | `bug.patch` 与 `fix.patch` | 补丁、构建和测试 |
| 新特性开发实例构造 | 基于已有抽象接口或基类，要求开发一种新的实现 | `instances/feature_implement/` | 完整仓库快照 `repo/` | 静态结构验收 |

两类任务共享 `parser.py` 提取的 ArkTS/ETS 结构信息，但任务目标和 instance 产物不同。错误实例不会复制原始仓库，错误代码通过补丁表达；新特性实例会复制一份未修改的基线仓库，供开发者直接实现功能。

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

## 2. 新特性开发实例构造

新特性开发实例面向“在现有抽象能力上增加一种实现”的开发任务。它不会向仓库注入错误，也不提供参考修复补丁，而是保存一份原始基线仓库，并通过已有实现文件和静态验收条件约束开发范围。

### 2.1 模块组成

- `feature_instance.py`：发现抽象节点及其实现/使用文件，生成仓库快照、任务描述、元数据，并执行静态结构验收。
- `parser.py`：直接解析目标仓库，或为外部传入的语法树 JSONL 提供结构数据。
- `../../tools/build_feature_instance.py`：列出候选节点或生成新特性开发 instance。
- `../../tools/verify_feature_instance.py`：验证开发后的仓库是否新增了符合要求的实现文件。
- `../../tests/test_feature_instance.py`：覆盖显式继承、结构化使用、导入别名、同名接口隔离、实例生成和验收逻辑。

### 2.2 候选抽象节点

`find_feature_candidates()` 从语法树中收集 `interface` 和 `class` 节点，再查找位于其他源码文件中的实现或使用关系。默认只保留至少关联两个实现/使用文件的节点。

支持两类关系：

1. **显式继承或实现**：源码中的 `class` 或 `struct` 通过 `implements` 或 `extends` 使用目标抽象节点；
2. **接口结构化使用**：文件从目标接口的定义文件导入该接口，并在 import 之外的实际代码中再次使用该类型。

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

### 2.4 生成 instance

使用默认输出目录生成 instance：

```bash
python3 tools/build_feature_instance.py test_project/Wechat_HarmonyOS
```

默认生成结构：

```text
instances/feature_implement/<instance-id>/
├── repo/          # 未修改的基线仓库快照
├── instance.json  # 候选、需求和静态验收元数据
└── task.md        # 面向开发者的任务说明
```

生成器使用 `shutil.copytree()` 保存仓库快照，同时跳过 `.git`、`.hvigor`、`.idea`、`.preview`、`.vscode`、`build`、`node_modules` 和 `oh_modules` 等版本控制、依赖或构建目录。原始仓库不会被修改。

输出目录必须位于源仓库之外，且目标 instance 目录不能已经存在。自定义 `instance-id` 只能包含字母、数字、点、下划线和连字符，避免生成到输出目录之外。

主要参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--syntax-tree` | 不指定 | 语法树 JSONL；省略时直接解析仓库 |
| `--output` / `--output-dir` | `instances/feature_implement` | instance 父目录 |
| `--instance-id` | 自动生成 | instance 目录名 |
| `--min-implementations` | `2` | 候选至少关联的实现/使用文件数 |
| `--list` | 关闭 | 只输出候选 JSON，不生成 instance |

### 2.5 Instance 元数据

`instance.json` 的核心字段包括：

| 字段 | 内容 |
| --- | --- |
| `task_type` | 固定为 `new_feature_development` |
| `source_repo` | 原仓库路径和 Git commit |
| `snapshot.repo` | 基线仓库快照位置 |
| `target.abstract_node` | 目标接口或基类信息 |
| `target.implementation_files` | 基线实现/使用文件及关系类型 |
| `feature_request` | 功能标题、任务描述和最低实现文件数量 |
| `affected_modules` | 可供开发者参考的现有实现/使用文件 |
| `acceptance.checks` | 静态结构验收条件 |

假设基线包含三个实现/使用文件，生成的需求会把 `required_implementation_file_count` 设置为 4，要求开发者新增一个独立源码文件，而不是只修改现有实现。

`task.md` 会明确说明目标抽象节点、现有参考文件和以下验收要求：

- 保留目标抽象节点；
- 实现/使用文件总数达到基线数量加一；
- 至少出现一个不在基线列表中的新源码文件；
- 保持现有实现的对外行为不变。

### 2.6 静态结构验收

开发者在 instance 的 `repo/` 中完成实现后，可以运行：

```bash
python3 tools/verify_feature_instance.py \
  instances/feature_implement/<instance-id>
```

`verify_feature_instance()` 会重新解析快照仓库并分别检查：

1. `abstract_node_exists`：原接口或基类仍然存在，路径、节点类型和名称保持一致；
2. `implementation_file_count`：当前实现/使用文件数量达到需求中的最低值；
3. `new_implementation_file`：至少有一个实现文件不在基线文件列表中。

三个检查全部通过时返回 `passed: true`，CLI 退出码为 0；否则返回 `passed: false`，CLI 退出码为 1。刚生成但尚未开发的基线 instance 验证失败是预期行为，因为此时还没有新增实现文件。

当前示例位于：

```text
instances/feature_implement/wechat_harmonyos-chatcontentitemdata-new-implementation/
```

### 2.7 Python API

```python
from arkts_syntax_tree import (
    create_feature_instance,
    find_feature_candidates,
    verify_feature_instance,
)

candidates = find_feature_candidates(
    "test_project/Wechat_HarmonyOS",
    min_implementation_files=2,
)

metadata = create_feature_instance(
    "test_project/Wechat_HarmonyOS",
    output_dir="instances/feature_implement",
)

result = verify_feature_instance(
    "instances/feature_implement/"
    "wechat_harmonyos-chatcontentitemdata-new-implementation"
)
```

### 2.8 验证测试

运行新特性实例专项测试：

```bash
PYTHONPATH=src python3 -m unittest tests.test_feature_instance -v
```

运行全部测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

### 2.9 当前边界

- 当前只扫描 `.ets` 和 `.ts` 文件；
- import 解析以相对路径为主，不解析工程路径别名、动态 import 或跨包 re-export；
- 显式关系通过源码结构模式识别，不等价于完整 TypeScript/ArkTS 类型检查；
- 接口结构化使用以“正确导入并在有效代码中再次引用”为判断依据，不能证明对象完整实现了接口中的所有成员；
- 静态验收确认新增文件和结构关系，不检查业务语义、UI 效果、运行时行为或性能；
- 生成器不会自动运行目标 HarmonyOS 工程的构建和测试，最终 instance 仍应结合项目自身验证流程进行人工确认。
