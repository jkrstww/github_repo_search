# Django-11265 轨迹评测报告

## 1. 总体结论

任务的技术核心是：`Query.split_exclude()` 为 `exclude()` 创建内层 `Query` 时，没有继承外层查询中的 `_filtered_relations`。因此，内层查询解析 `book_alice__isnull` 时无法识别 `FilteredRelation` 别名，触发 `FieldError`。多数轨迹最终都围绕以下修复展开：

```python
query._filtered_relations = self._filtered_relations.copy()
```

部分模型进一步修改了 `trim_start()`、连接对象比较或查询状态复制逻辑。

成功与失败轨迹的主要差异不是“是否找到了一行修复”，而是验证闭环是否完整：

- 成功轨迹通常完成了精确问题复现，确认了异常或错误结果的根因。
- 成功轨迹通常检查了实际查询结果，部分还打印了生成 SQL。
- 成功轨迹运行了 `filtered_relation` 测试，并补充了 `queries`、聚合、注解或边界场景。
- 失败轨迹中，有些也得到了看似正确的本地测试结果，但测试覆盖不足、测试被删除、输出被截断，或补丁复杂度超过了验证证据。
- `resolved` 与本地过程质量并非完全一致：Sonnet 4.5、Opus 4.6、Minimax 2.5 都有较强的本地测试证据，但外部标签仍为 `false`。这说明隐藏评测可能覆盖了未验证的 SQL、别名、连接裁剪或补丁契约，也说明不能把“某条测试命令返回 0”直接等同于任务完成。

样本中：

- `resolved: true`：4 条
- `resolved: false`：7 条

成功轨迹平均更重视行为级验证；失败轨迹更常见的问题是验证范围不足或对复杂修改缺乏针对性证据。不过样本只有 11 条，不能据此归因于模型本身，也无法排除模型版本、提示配置、随机性和测试环境差异的影响。

## 2. 可复用的 100 分轨迹评分细则

| 维度 | 权重 | 评分档位 | 可观察证据 | 主要扣分条件 |
|---|---:|---|---|---|
| 任务理解与验收标准 | 15 | 0/5/10/13/15 | 能否复述 `exclude()` + `FilteredRelation` 的精确失败行为；是否明确预期结果、异常消失和回归要求 | 只复述文件名；只关注 `FieldError` 文本；没有定义成功标准 |
| 根因定位与代码推理 | 20 | 0/8/14/18/20 | 是否跟踪 `build_filter()` → `split_exclude()` → 新 Query → `names_to_path()` 的数据流；是否识别 `_filtered_relations` 丢失 | 盲目修改解析器或 SQL；没有解释为什么内层 Query 需要外层状态 |
| 修改正确性与范围控制 | 25 | 0/10/17/22/25 | 修复是否保留 filtered-relation 上下文；是否避免共享可变对象；修改是否局部、符合现有 Query clone 模式；复杂改动是否有必要 | 修改测试或配置代替源代码；复制无关状态；复杂改动未证明；引入新的连接/别名风险 |
| 测试设计与验证证据 | 25 | 0/8/15/20/23/25 | 精确复现；实际返回结果；生成 SQL；官方 `filtered_relation`；`queries`/聚合/注解；M2M、别名、链式 exclude 等边界 | 仅导入模块；只跑普通测试；只看截断输出；删除失败测试后继续；没有行为断言 |
| 错误恢复与迭代 | 10 | 0/4/7/9/10 | 能区分环境错误、补丁错误和测试错误；失败后缩小假设；回退无效复杂方案 | 失败后直接删除测试；反复试错但不更新根因模型；把环境问题当成功 |
| 收尾、补丁和环境卫生 | 5 | 0/2/4/5 | 最终 diff 仅包含目标源文件；补丁可审阅；明确最终测试和残余风险 | 没有最终 diff；提交前仍有临时文件影响；没有说明未验证部分 |

这样设计的原因是：该任务的难点不在于写出一行代码，而在于维护查询状态的一致性并证明不会破坏现有 SQL 生成逻辑。因此“修改正确性”和“测试证据”合计占 50 分；`resolved` 只能作为外部结果标签，不能替代过程评分。

## 3. 具体案例

### 成功案例：`20260217_mini-v2.0.0_gpt-5-2-high.json`

- 第 2 步完整读取了 PR 描述，并识别问题发生在 `split_exclude()` 创建内层查询时。
- 第 40 步打印了实际 SQL，结果为：
  - 外层查询排除一个子查询；
  - 子查询包含 `INNER JOIN filtered_relation_book`；
  - 条件中包含 `title LIKE poem by alice`；
  - 实际结果为 `['Jane']`。
- 第 52 步运行 `filtered_relation` 测试并返回码 0。
- 第 53 至 56 步检查了最终 diff，确认核心修改为复制 `_filtered_relations`，同时对 `trim_start()` 的 filtered-relation 路径裁剪条件进行了调整。

这些证据同时覆盖了根因、实际 SQL 结构、用户可见结果和回归测试，因此该轨迹不仅“没有报错”，还证明了子查询确实保留了过滤连接。其外部结果为 `resolved: true`。

需要注意的是，该轨迹的补丁比最小一行修复更复杂；虽然有 SQL 证据支持，但额外的 `trim_start()` 修改仍应通过更广泛的数据库后端测试。

### 失败案例：`20260217_mini-v2.0.0_gemini-3-flash-high.json`

- 第 42 至 48 步建立了基本复现，基本 `exclude()` 场景通过。
- 第 49 步新增 M2M exclude 场景时出现：
  `sqlite3.OperationalError: no such column: U5.title`
- 后续第 50 至 51 步继续修改测试脚本，但 M2M 场景仍然失败。
- 最终第 52、55 步显示补丁主要仍是一行 `_filtered_relations` 复制，未能解释或修复 M2M 失败。

这条轨迹证明了基本场景已改善，但也暴露出连接别名或 JOIN 裁剪在更复杂关系上的风险。它没有完成失败场景的定位，也没有给出足够证据证明隐藏场景安全，因此外部 `resolved: false` 与过程证据一致。该案例说明：基本 PR 场景通过不能推出所有关系类型都正确。

## 4. 逐条轨迹诊断

| 轨迹文件 | 过程质量摘要 | 最终 `resolved` |
|---|---|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | 77 步。成功复现 `FieldError`，多次尝试和回滚，曾出现错误 SQL、错误结果及环境问题。后期 `filtered_relation` 测试通过，但调试过程曲折，最终证据部分被 `tail` 截断。过程质量中上，恢复能力较强，验证完整性一般。 | `false` |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | 48 步。定位清楚，最终使用一行 `_filtered_relations.copy()`。第 42 步精确 PR 场景通过，第 41 步 31 个 filtered-relation 测试通过，第 43 步 364 个 queries 测试通过。但第 38 步临时最终验证出现失败，随后删除临时测试；缺少对失败原因的完整解释。 | `false` |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | 27 步。定位和修改很快；第 22 步 filtered-relation 测试通过，第 24 步 queries 364 项通过。早期因依赖缺失导致导入失败，且没有可靠展示独立精确复现。测试面较好，但行为证据和收尾解释不足。 | `false` |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | 56 步。基本场景通过，但第 49 至 51 步 M2M exclude 持续出现 `no such column: U5.title`。失败边界没有修复或解释，最终仍保留较小补丁。 | `false` |
| `20260217_mini-v2.0.0_glm-5-high.json` | 83 步。完成真实 `FieldError` 复现，尝试复杂 `trim_start()` 修复后回退到更局部方案。第 66 步自定义综合测试通过，第 67 步 filtered-relation 测试通过，第 76 步 queries 测试通过，并覆盖复杂条件、多关系和不同 lookup。迭代充分，最终补丁相对克制。 | `true` |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | 57 步。除了复制 `_filtered_relations`，还调整 `trim_start()`，并通过第 40 步 SQL 和实际结果 `['Jane']` 证明子查询 JOIN 正确。第 52 步官方 filtered-relation 测试通过。直接证据最强，但补丁复杂度和回归风险高于最小修复。 | `true` |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | 22 步。修改范围最大，复制 annotations、extra selects、mask 等多类状态；第 18 步仅模块导入失败，第 20 步安装 `pytz` 后导入成功。没有完成精确行为测试、SQL 检查或官方测试，属于“代码写出但未验证”。 | `false` |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | 116 步。调试最深入，最终使用 `self.__class__`、复制 `_filtered_relations`，并扩展 `trim_start()` 处理 filtered relation 条件、别名和引用计数。第 99、104 步精确场景和边界行为通过，第 106 步 filtered-relation、第 108 步 queries、第 111 至 113 步聚合、注解及边界测试通过。证据最丰富，但补丁复杂，存在过度修改风险。 | `true` |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | 64 步。一行核心修复，filtered-relation 31 项、queries/aggregation/expressions/annotations 合计 599 项通过，并做了 M2M 和 lookup 测试。过程简洁且覆盖较广，但没有展示精确 PR 场景的独立复现、SQL 或隐藏边界验证。 | `false` |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | 43 步。第 15、26 步先复现失败，第 33 步精确测试 3 项通过，第 34 步 filtered-relation 测试通过。之后主要进行静态搜索和 diff 检查，缺少 queries、M2M、聚合等广泛回归验证。 | `false` |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | 97 步。初始完整测试失败，随后加入 `_filtered_relations` 复制、`Join.equals`/`BaseTable.equals` 和 `trim_start()` 的模型、基表、条件处理。第 90 步精确测试通过，第 91 步 filtered-relation、第 93 步 queries 通过。修复证据较强，但跨多个底层类的改动明显扩大回归面。 | `true` |

过程质量评分建议如下：

| 轨迹 | 过程分数 / 100 | 评价 |
|---|---:|---|
| Claude Opus 4.5 | 78 | 定位和恢复较好，证据不够稳定 |
| Claude Sonnet 4.5 | 84 | 精确场景和回归测试较强，但删除失败临时测试削弱可信度 |
| Claude Opus 4.6 | 80 | 高效、测试较广，精确行为证据不足 |
| Gemini 3 Flash | 70 | 基本修复有效，但 M2M 失败未闭环 |
| GLM-5 | 91 | 根因、边界、回归和收尾均较完整 |
| GPT-5.2 | 93 | 有直接 SQL 和结果证据，但补丁偏复杂 |
| GPT-5 mini | 38 | 主要完成静态修改和导入验证 |
| Kimi K2.5 | 94 | 验证最全面，复杂补丁需审慎评估 |
| Minimax 2.5 | 86 | 测试数量多，但精确场景证据不足 |
| Gemini 3 Pro | 74 | 能复现并修复基本问题，回归覆盖有限 |
| Gemini 3.5 Flash | 90 | 迭代和测试充分，但底层改动范围过大 |

这些分数刻意不与 `resolved` 强制一致。例如 Sonnet 和 Minimax 的过程分数较高但外部结果为 `false`，这正体现了过程质量与最终评测结果应分开记录。

## 5. 可执行改进建议

### 对轨迹执行策略

1. 第一轮就建立最小、可重复的精确测试：
   - 一个匹配作者；
   - 一个不匹配作者；
   - `annotate(FilteredRelation(...)).exclude(alias__isnull=False)`；
   - 同时断言“不抛 `FieldError`”和结果集合。

2. 在修复后同时检查三类证据：
   - 实际结果；
   - `str(qs.query)` 生成的 SQL；
   - 官方相关测试。

3. 对 `split_exclude()` 明确列出需要继承的 Query 状态，优先参考现有 `clone()` 的复制模式，而不是一次性复制大量无关属性。

4. 对任何 `trim_start()`、`Join.equals()`、`BaseTable.equals()` 改动，必须增加针对：
   - ForeignKey；
   - ManyToMany；
   - 多个 filtered relation；
   - 链式 `exclude()`；
   - OR 条件；
   - nullable/louter join；
   - 聚合和注解；
   的行为测试。

5. 测试失败时先区分：
   - 依赖或设置错误；
   - 测试脚本错误；
   - SQL 生成错误；
   - 真实回归。
   
   不应仅删除失败测试来恢复绿色结果。

6. 避免只保留 `tail`、`head` 或 `grep` 后的输出作为最终证据；应保留测试总数、失败名称、异常栈和关键 SQL。

7. 收尾前执行干净的源代码 diff 检查，确认只修改目标非测试文件，并记录未覆盖的风险场景。

### 对评测流程

1. 隐藏评测应明确覆盖基本 PR 场景和 M2M/别名/连接裁剪场景，否则“本地通过但 `resolved=false`”难以诊断。
2. 除二值 `resolved` 外，保存测试命令、补丁 diff、失败测试名称和最终 SQL，便于区分模型推理失败与环境失败。
3. 对复杂补丁增加静态范围检查，避免通过修改多个底层类掩盖一个局部状态继承问题。
4. 对每条轨迹分别记录“过程质量分数”和“最终外部结果”，不要用单一标签替代前者。

## 6. 不能仅凭这些轨迹确定的结论

- 不能确定某个模型在一般 Django 修复任务中必然优于其他模型；样本量太小，且模型版本、提示、运行时和随机性混杂。
- 不能确定 `resolved=false` 的所有轨迹都产生了错误补丁；其中若干轨迹的本地测试确实通过，失败可能来自隐藏测试、补丁契约、环境差异或未覆盖场景。
- 不能仅凭 `returncode 0` 断言测试充分，因为有些命令截断了输出，或只运行了局部测试。
- 不能确定复杂 `trim_start()`、`Join.equals()` 和 `BaseTable.equals()` 修改在所有数据库后端上安全；现有证据主要来自 SQLite。
- 不能确定临时测试文件是否被评测环境纳入或排除；轨迹中多次创建、删除测试文件，因此测试状态本身不完全等价于干净仓库。
- 不能仅凭最终 diff 判断语义正确性；必须结合查询结果、生成 SQL 和关系类型覆盖。
- 不能把步数、token 消耗或成本直接解释为质量；Kimi 步数最多但验证最完整，GPT mini 步数最少但证据明显不足，说明效率指标与正确性并不等价。
