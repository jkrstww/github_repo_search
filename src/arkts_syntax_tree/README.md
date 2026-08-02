# ArkTS AST 错误实例构造

本模块面向 HarmonyOS ArkTS/ETS 项目，提供轻量抽象语法树提取，以及基于语法树自动选择函数、注入返回值错误并生成可修复 instance 的能力。整个流程不会修改原始 clone，错误只写入独立仓库快照。

## 模块组成

- `parser.py`：扫描 `.ets`、`.ts` 文件，提取 imports、函数和容器节点、源码范围、修饰符及基础 metrics。
- `bug_instance.py`：构建候选函数集合，分析调用出度和跨文件返回值消费者，执行 mutation，并生成快照、补丁和元数据。
- `../../tools/parse_arkts_syntax_tree.py`：生成语法树 JSONL 和汇总 JSON 的命令行入口。
- `../../tools/build_arkts_bug_instance.py`：查看候选或生成错误 instance 的命令行入口。
- `../../tests/test_bug_instance.py`：候选识别、错误注入、换行保留和补丁方向的自动化测试。

## 工作流程

### 1. 提取语法树

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

### 2. 构建候选函数

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

### 3. 识别跨文件消费者

分析器利用 AST JSONL 中的 import 信息解析相对导入，并为 named、default、namespace 和别名导入建立调用匹配规则。调用点必须位于候选函数所在文件之外。

当前认为以下情况实际消费了返回值：

- 赋值给变量，而且该变量在所属函数的后续代码中被使用；
- 直接用于 `if`、`while`、`for` 等条件；
- 直接作为当前函数的返回值；
- 作为另一个函数调用的参数；
- 进入 Promise `.then()` 链。

裸调用以及“赋值后从未使用”的调用不会计入下游消费者。多个调用点落在同一个下游函数时，只计为一个消费者。

### 4. 选择并注入 mutation

分析器从候选函数末尾向前查找可变异的 `return`，然后根据返回类型和原表达式推断错误值：

| 返回类型或表达式 | 注入值 |
| --- | --- |
| `boolean`、`Promise<boolean>` 或布尔表达式 | `false`，原值为 `false` 时使用 `true` |
| `string` | `""` |
| `number` | `0` |
| 数组 | `[]` |
| 其他类型 | `null` |

候选函数按下游消费者数量、唯一调用出度、文件路径和源码行号排序。默认选择排名最高的候选，只替换目标返回语句所在的一行，并保留原文件的 LF 或 CRLF 换行格式。

### 5. 生成 instance

`create_bug_instance()` 将原仓库复制到独立目录，然后只在快照中应用 mutation。复制时跳过 `.git`、`build`、`.hvigor`、`node_modules`、`oh_modules` 等版本控制、构建和依赖目录。

输出目录必须位于源仓库之外，且不能与已有 instance 重名。默认结构如下：

```text
instances/<instance-id>/
├── repo/          # 已注入错误的仓库快照
├── bug.patch      # 原始代码 -> 错误代码
├── fix.patch      # 错误代码 -> 原始代码
└── instance.json  # instance 元数据和任务描述
```

`instance.json` 包含：

- instance ID 和创建时间；
- 原仓库绝对路径和 Git commit；
- 语法树 JSONL 路径；
- 目标函数、源码范围、signature 和 modifiers；
- 唯一调用出度和 callee 列表；
- 跨文件消费函数、调用位置和消费类型；
- mutation 前后的表达式与源码行；
- 原文件和错误文件的 SHA-256；
- 包含所有下游消费函数的任务 `description`。

## 命令行使用

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
| `--output-dir` | `instances` | instance 父目录 |
| `--min-out-degree` | `3` | 最小唯一调用出度 |
| `--min-consumers` | `2` | 最小跨文件下游消费者数量 |
| `--instance-id` | 自动生成 | instance 目录名 |
| `--list-candidates` | 关闭 | 只输出候选 JSON，不生成快照 |

在 Windows 工作区中可通过 WSL 执行同一命令：

```powershell
wsl bash -lc "python3 tools/build_arkts_bug_instance.py test_project/Wechat_HarmonyOS --syntax-tree syntax_trees/Wechat_HarmonyOS_syntax_tree.jsonl --list-candidates"
```

## Wechat 示例

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
instances/wechat-permission-request-return-false/
```

## 验证

运行测试：

```bash
python3 -m unittest tests.test_bug_instance
```

在错误仓库快照中检查修复补丁是否可应用：

```bash
cd instances/wechat-permission-request-return-false/repo
patch --dry-run -p1 -i ../fix.patch
```

`--dry-run` 只验证补丁，不会修改错误快照。

## 当前边界

当前实现是轻量 AST 与源码静态分析工具，不是完整 ArkTS 编译器或全程序数据流引擎：

- 主要解析相对 import，不解析工程路径别名、动态 import 或运行时注入；
- 调用和返回值消费使用源码模式匹配，复杂跨行表达式可能无法识别；
- 不处理反射、动态属性访问和运行时多态调用关系；
- mutation 基于返回类型和表达式启发式推断，不保证每个错误都能通过编译；
- instance 生成阶段不会自动执行目标 HarmonyOS 项目的构建或测试。

因此，生成 instance 后仍应结合 `fix.patch` dry-run、项目构建和测试结果进行最终确认。
