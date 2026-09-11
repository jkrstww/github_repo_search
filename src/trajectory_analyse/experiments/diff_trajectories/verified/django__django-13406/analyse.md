# Django #13406 轨迹评测报告

## 1. 总体结论

本目录包含 11 条 ATIF 轨迹，其中：

- `resolved=true`：3 条（27.3%）
- `resolved=false`：8 条（72.7%）

成功轨迹：

- `20260217_mini-v2.0.0_claude-4-5-opus-high.json`
- `20260217_mini-v2.0.0_claude-4-6-opus.json`
- `20260217_mini-v2.0.0_kimi-k2-5-high.json`

失败轨迹：

- 其余 8 条。

### 1.1 成功轨迹的共同特征

成功轨迹最终都识别出同一个关键不变量：

> `QuerySet.query` 被替换为一个包含非空 `values_select` 的 `Query` 时，必须同步把 QuerySet 的 `_iterable_class` 设置为 `ValuesIterable`。

成功实现普遍非常小，只修改 `django/db/models/query.py` 中的 query setter：

```python
@query.setter
def query(self, value):
    if value.values_select:
        self._iterable_class = ValuesIterable
    self._query = value
```

其共同过程特征是：

1. 能准确理解问题不是 SQL 结果错误，而是 QuerySet 迭代器状态丢失。
2. 能定位到 `QuerySet.query` setter、`values_select` 和 `_iterable_class` 的关系。
3. 至少一次复现“原始 QuerySet 返回 dict，重新赋值 pickled query 后返回模型实例”的行为。
4. 修改范围小，避免改变 Query 的序列化结构。
5. 能运行针对性测试及一组回归测试。
6. 最终 patch 内容与实际修改一致。

### 1.2 失败轨迹的共同问题

失败轨迹并非全部“完全没有工作”。其中多条轨迹完成了合理分析，甚至通过了本地测试，但最终 `resolved=false`。主要差异如下。

#### 过度扩展修复范围

多条失败轨迹试图把 `values()`、`values_list()`、`flat=True`、`named=True` 的迭代器类完整写入 `Query`，例如：

- 增加 `_iterable_class_name`
- 增加 `values_list_type`
- 增加 `values_iterable_class`
- 增加 `values_fields`
- 在 query setter 中恢复 `_fields`、缓存、prefetch 状态等

这类实现看起来更完整，但引入了额外的序列化字段、状态同步逻辑和兼容性假设。当前任务的外部评测显然更偏向于 Django 现有设计中的最小修复。

#### 对正常 QuerySet 状态的处理不一致

若 query setter 只在发现元数据时设置迭代器，却不在普通 Query 上恢复 `ModelIterable`，则以下状态转换可能残留旧状态：

1. 目标 QuerySet 原本是 `values()`；
2. 再赋值一个普通 Query；
3. 目标 QuerySet 仍然使用 `ValuesIterable`。

Sonnet、Minimax 等轨迹的实现存在类似风险。反过来，某些实现无条件重置 `_fields`、缓存或 deferred filter，也可能改变原有 API 行为。

#### 测试结果和真实证据不总是一致

多个轨迹的命令返回码为 0，但输出中包含：

- `Traceback`
- 测试加载错误
- `ImportError`
- `FAILED`
- 管道命令掩盖的失败

例如使用 `tail`、`grep` 或错误的测试标签时，shell 最终返回码不能充分证明测试成功。因此“命令返回 0”只能算中等强度证据，不能替代明确的 `Ran N tests ... OK` 或针对性断言输出。

#### 不完整或错误的 patch 生成

GPT-5-mini 的轨迹尤其典型：它先后生成了两个 git commit，但最终使用两个 commit 之间的 diff 作为提交 patch。最终 patch 中 query setter 读取 `_values_iterable_class`，却没有包含 `values()`/`values_list()` 写入该属性的修改，因此修复链条是不完整的。

### 1.3 证据强弱与局限性

证据强度可分为：

- 强证据：复现脚本明确展示修复前后对象类型和结果变化；测试输出明确显示 `Ran N tests ... OK`；最终 diff 与实现一致。
- 中等证据：较大测试集通过，但未验证任务核心场景，或输出经过 `tail`/`grep` 处理。
- 弱证据：仅有模型自述“修复成功”、patch 已生成，或命令返回码为 0 但输出包含异常。

本报告不能仅凭轨迹确定每条失败轨迹在外部评测中具体失败的测试断言，因为目录中没有评测器的失败日志。对失败原因的判断分为：

- 直接可观察的问题，例如 patch 缺少元数据写入、命令明显失败；
- 基于 patch 结构推断的高概率风险，例如额外状态字段、状态重置和兼容性问题。

此外，本样本只有一个 Django 任务，不能据此推断模型在一般软件工程任务中的总体能力。模型版本、运行环境、依赖预装情况和轨迹长度也存在混杂因素。

## 2. 可复用的 100 分轨迹评测细则

### 2.1 评分维度

| 维度 | 权重 | 满分表现 | 可观察证据 |
|---|---:|---|---|
| 任务理解与验收标准 | 15 | 准确解释问题机制，明确预期输入、输出和回归边界 | 是否区分 Query 与 QuerySet；是否明确 dict/model instance 类型差异 |
| 定位与因果假设 | 15 | 定位到真正的状态源和调用链，假设可验证 | 是否检查 `query` setter、`values_select`、`_iterable_class`、`values()` |
| 工具与命令选择 | 10 | 命令针对性强，输出可解释，少用无效或破坏性操作 | 搜索、局部读取、测试标签、返回码和 stderr 是否被正确处理 |
| 代码修改正确性与范围 | 25 | 修复机制正确、改动最小、保持现有 API 和状态语义 | patch 是否只改必要源文件；是否引入不必要序列化字段或状态耦合 |
| 测试设计与验证 | 20 | 先复现，再验证修复，再做相关回归测试 | 是否验证 pickled `Query` 重新赋值；是否测试 annotate、普通 Query 和相关 Query |
| 错误恢复与迭代 | 10 | 能识别依赖、路径、测试标签和实现错误并有效修正 | 是否根据 traceback 改变方案；是否重复验证，而不是忽略失败 |
| 最终收尾与证据完整性 | 5 | 清理临时文件，确认 diff，提交内容与实际修改一致 | `git diff`、状态检查、patch 内容和最终命令是否一致 |

### 2.2 评分档位

每个维度按以下档位取该维度权重的比例：

- 100%：完整、直接、证据充分。
- 75%：基本正确，有少量遗漏或低风险瑕疵。
- 50%：部分完成，存在明显证据缺口或设计风险。
- 25%：仅有零散尝试，核心目标未被可靠覆盖。
- 0%：方向错误、没有相关工作，或结果不可用。

总分为各维度得分之和：

- 90–100：优秀，可复用性和证据链都很强。
- 75–89：良好，核心工作完成，但有局部风险。
- 60–74：勉强可接受，存在明显验证或设计缺口。
- 40–59：较弱，过程有大量无效工作或实现风险。
- 0–39：失败，未形成可验证的解决方案。

### 2.3 扣分条件

以下情况应在相应维度扣分：

- 只根据 PR 描述猜测，没有查看相关实现：任务理解或定位维度扣 25%–50%。
- 复现脚本因路径、依赖或 app label 失败，却继续声称已复现：测试维度扣 25%–50%。
- 测试命令输出包含失败，但只看 shell 返回码：工具或测试维度扣 10%–30%。
- 添加与任务无关的公共状态、序列化字段或缓存重置：实现正确性维度扣 10%–40%。
- 没有验证普通 Query、values Query 和重新赋值场景之间的状态切换：实现或测试维度扣 10%–25%。
- 最终 diff 遗漏必要修改，或 patch 与工作树不一致：最终收尾维度至少扣 50%。
- 大量重复、格式错误或无法执行的命令：工具选择和错误恢复维度扣 10%–50%。
- 测试只覆盖宽泛回归集、没有覆盖任务核心行为：测试维度最高只能给 50%–75%。

### 2.4 设计理由

实现正确性和测试验证共占 45%，因为软件工程任务的核心是产生行为正确且可验证的修改。任务理解、定位和工具选择共占 40%，用于区分“碰巧通过”与真正理解代码机制的过程。错误恢复和最终收尾权重较低，但它们决定结果是否可复现、是否能被可靠提交。

## 3. 具体案例

### 案例 A：Claude Opus 4.6，成功

文件：`20260217_mini-v2.0.0_claude-4-6-opus.json`

关键事件：

- step 7–9：检查 `QuerySet.query` setter、`_iterable_class`、`_fields`，明确判断这些状态属于 QuerySet 而不是 Query。
- step 10：提出在 query setter 中根据 `value.values_select` 恢复 `ValuesIterable`。
- step 24：验证实际 PR 场景，确认重新赋值 pickled query 后返回 dict。
- step 30：运行 `queryset_pickle queries expressions`，明确得到 `Ran 577 tests ... OK`。
- step 32–33：生成并提交仅包含两行逻辑的 patch。

该轨迹的证据链完整：问题定位正确、修复最小、针对性验证和大范围回归测试均通过。`resolved=true` 与过程证据一致。按评分细则估计为 92/100。

### 案例 B：Claude Sonnet 4.5，失败但过程并非无效

文件：`20260217_mini-v2.0.0_claude-4-5-sonnet-high.json`

关键事件：

- step 36–41：多次复现脚本因错误 app 配置、模型注册和测试方法而失败。
- step 39–55：添加测试后发现测试插入位置错误，随后调整并使 queryset_pickle 测试通过。
- step 56–59：额外测试 `values_list()`、`flat=True`、`named=True` 以及普通 Query 反向赋值。
- 最终 patch 在 `Query` 中添加 `_iterable_class_name`，并在 query setter 中根据字符串恢复不同迭代器。

过程上，它确实理解了问题并进行了较广泛测试；但实现超出了当前问题的最小范围，而且 query setter 在没有元数据时没有明确恢复 `ModelIterable`。这会使旧状态残留成为潜在回归点。轨迹报告了很多本地测试通过，但外部 `resolved=false`，说明本地测试覆盖不足以证明实现符合评测要求。按细则估计为 73/100。

### 案例 C：GPT-5-mini，失败且 patch 链条不完整

文件：`20260217_mini-v2.0.0_gpt-5-mini.json`

关键事件：

- step 1–10：进行了相关代码搜索和局部阅读。
- step 11–25：修改 QuerySet/Query 设计，但没有看到有效的针对性测试执行。
- step 31：git 日志显示先生成了“记录 values iterable”的 commit，再生成“恢复 iterable”的 commit。
- step 32：最终 patch 只包含 query setter 读取 `_values_iterable_class` 的修改，没有包含 producer 端 `values()`/`values_list()` 写入该属性的修改。
- step 33：直接提交该 patch。

这是一个可直接观察的逻辑缺陷：读取端和写入端不匹配。即使 setter 代码本身看似合理，pickled Query 中也不会出现它要读取的元数据。`resolved=false` 与该证据高度一致。按细则估计为 48/100。

## 4. 每条轨迹诊断摘要

以下分数是依据过程证据的人工估分，不等同于外部 `resolved` 标签。

| 轨迹文件 | 模型 | 过程质量估分 | `resolved` | 诊断摘要 |
|---|---|---:|:---:|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | Claude Opus 4.5 | 87 | true | 前期因缺依赖和 app label 复现失败，但能恢复；随后用正确测试基础设施复现并采用最小两行修复，33 个 queryset_pickle 测试及相关测试通过。 |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | Claude Sonnet 4.5 | 73 | false | 理解问题正确，补充了多种 values_list 测试；过程较长且多次环境/测试定位错误，最终引入 Query 元数据和字符串映射，范围偏大且普通 Query 状态恢复不完整。 |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | Claude Opus 4.6 | 92 | true | 定位最清晰，直接识别 QuerySet 状态丢失；虽有少量环境脚本失败，但最终验证了核心场景并通过 577 个相关测试，patch 最小。 |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | Gemini 3 Flash | 74 | false | 进行了修复前后复现，并测试了 values、values_list、flat、named 等变体；但 patch 把迭代器类和 `_fields` 写入 Query，增加了序列化耦合，且有多次环境和命令错误。 |
| `20260217_mini-v2.0.0_glm-5-high.json` | GLM-5 | 52 | false | 代码阅读较充分，也通过了 queryset_pickle、queries、expressions、aggregation 等套件；但 168 步中约 81 次非零或命令解析失败，多个 `[[...]]` 命令明显无效，最终实现复杂且核心复现证据不足。 |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | GPT-5.2 | 67 | false | 进行了较深入的代码阅读并设计 `_restore_queryset_state`，考虑了缓存、prefetch 和 `_fields`；但实现范围很大，轨迹中缺少修复后的核心行为测试，外部标签无法由本地证据支持。 |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | GPT-5-mini | 48 | false | 方向上知道需要保存迭代器信息，但最终用 commit 间 diff 生成 patch，遗漏 `values()`/`values_list()` 写入元数据的修改；几乎没有有效测试证据。 |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | Kimi K2.5 | 80 | true | 轨迹很长、空 reasoning 较多，若干自定义复现和测试插入失败；但最终回到最小 query setter 修复，queries 测试套件通过，外部结果为成功。 |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | Minimax 2.5 | 82 | false | 成功运行了接近 PR 原文的复现，明确得到 dict 结果，也通过 queries、expressions、aggregation 测试；但引入 `_iterable_class` 字符串和 `set_values()` 改造，状态重置和兼容性风险较大。 |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | Gemini 3 Pro | 64 | false | 能复现原始错误并最终运行 queryset_pickle、model_regress；过程中出现多次环境错误和一次 `IndentationError`，最终 patch 只在目标仍为 `ModelIterable` 时切换到 `ValuesIterable`，覆盖条件较窄。 |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | Gemini 3.5 Flash | 84 | false | 代码探索最系统之一，添加并运行 values、values_list、flat、named、普通 Query 反向赋值测试，503 个相关测试通过；但最终采用跨 Query/QuerySet 的 `values_iterable_class`、`values_fields` 元数据方案，复杂度和隐藏兼容性风险可能导致外部失败。 |

## 5. 可执行改进建议

### 5.1 对模型执行流程

1. **先建立最小复现，再设计扩展方案**  
   复现应直接使用现有 Django 测试应用，避免动态 app、临时 settings 和未注册模型造成噪声。

2. **围绕现有状态不变量修改**  
   本任务的关键关系是：

   ```text
   Query.values_select 非空
   -> QuerySet 应使用 ValuesIterable
   ```

   应先验证是否只需在 query setter 恢复这一关系，再考虑是否真的需要扩展到 values_list 的完整类型保真。

3. **优先采用最小 patch**  
   不要在没有测试证明的情况下增加 Query 序列化字段、缓存重置、prefetch 状态恢复或新的公共内部协议。

4. **测试命令必须显式检查结果**  
   避免只依赖管道后的 shell 返回码。应直接保留：

   ```text
   Ran N tests ... OK
   ```

   对失败测试，应读取完整 traceback，而不是通过 `tail` 截断。

5. **区分环境失败与代码失败**  
   `ModuleNotFoundError`、模型注册失败和错误测试标签应单独修复；不能把环境脚本失败当作产品代码复现失败。

6. **最终提交前做一致性检查**  
   检查三者是否一致：

   - 工作树中的实际源代码；
   - `git diff`；
   - 最终输出的 patch。

   GPT-5-mini 的遗漏就是典型反例。

### 5.2 对评测基础设施

1. 记录外部评测失败的具体测试名称和断言，帮助区分“过度实现”和“基本逻辑错误”。
2. 对测试命令保留原始 stdout/stderr，不要只存经过 `tail` 或 `grep` 的结果。
3. 将“patch 是否包含必要 producer/consumer 两端修改”纳入静态检查。
4. 对本任务增加以下回归场景：
   - `values().annotate()` 的 pickled Query 重建；
   - `values_list()`；
   - 普通 Query 覆盖 values Query；
   - values Query 覆盖普通 Query；
   - pickled Query 在不同 QuerySet 初始状态上的赋值。
5. 把过程质量和最终正确性分开报告。`resolved=false` 不应抹去“完成了正确定位和有效测试”的过程价值，反之亦然。

## 6. 不能仅凭这些轨迹确定的结论

- 不能确定每个失败模型具体触发了哪一条隐藏测试失败，因为没有外部评测日志。
- 不能断言所有复杂实现一定错误；部分实现可能在更完整的测试集下成立，但当前样本中的外部结果没有支持它们。
- 不能据此建立模型能力的普遍排名。这里只有一个 Django 任务，且依赖安装、Python 版本、测试环境和轨迹长度不同。
- 不能把本地测试通过等同于补丁正确。多条失败轨迹都通过了数百个现有测试，但仍然得到 `resolved=false`。
- 不能仅凭 token、步数或成本推断成功率。Opus 4.6 步数较少且成功，但 Kimi 轨迹很长也成功；GLM 步数最多且失败，说明过程有效性比单纯长度更重要。
