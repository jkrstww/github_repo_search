# Django #12663 轨迹评测报告

## 评测范围与总体结论

目录中共有 11 条 ATIF-v1.7 轨迹：6 条 `resolved=true`，5 条 `resolved=false`。轨迹长度为 18 至 144 步，但步数、token 消耗和模型名称都不能单独解释成败。

任务的关键调用链是：

1. 嵌套 `Subquery` 的输出字段最终解析为 `User.id` 对应的 `AutoField`/`IntegerField`。
2. `filter(owner_user=user)` 将 `SimpleLazyObject(lambda: User(...))` 作为 RHS。
3. `Lookup.get_prep_lookup()` 调用输出字段的 `get_prep_value()`。
4. `IntegerField.get_prep_value()` 对 RHS 执行 `int(value)`。
5. `SimpleLazyObject` 能代理属性访问，但没有把 `int()` 自动转发给被包装对象，因此出现 `TypeError`。

成功轨迹通常具备以下特征：

- 复现了题目给出的完整嵌套查询，而不是只测试 `SimpleLazyObject(lambda: user.pk)`。
- 定位到 ORM 的 RHS 准备、LazyObject 解包和模型主键归一化路径。
- 在补丁后重新运行原始场景。
- 至少运行一组相关 Django 测试，且检查了 SQL 或结果。

失败轨迹的常见问题：

- 只增加 `LazyObject.__int__`，但被包装对象是 `User`，本身不能转换为整数。
- 将“lazy User 实例”和“lazy pk”混为一谈。
- 修改 `Subquery`/`Query`/`ForeignKey.get_col()` 等内部语义，却没有证明这是根因。
- 原始场景仍失败后，转而运行一个较容易通过的变体。
- 依赖 `head`/`tail` 管道，导致测试失败时 shell 返回码仍为 0。
- 补丁过宽，修改 Decimal、Float、Integer 等无关类型，增加隐藏回归风险。

因此，`resolved` 与过程质量只有中等相关性。`resolved=true` 说明外部评测接受了最终结果，但不等价于每一步都正确；`resolved=false` 也不表示整个分析过程毫无价值。

## 可复用的 100 分轨迹评测细则

评分采用 0/25/50/75/100 五档，按权重折算。总体原则是：实现正确性和验证证据占主要权重，步数、成本和模型名不计分。

| 维度 | 权重 | 评分档位与可观察证据 | 主要扣分条件 |
|---|---:|---|---|
| 任务理解 | 15 | 0：未理解任务；25：只复述报错；50：识别 `SimpleLazyObject` 与 `Subquery`；75：区分 lazy User 与 lazy pk；100：明确预期语义、复现条件和不应破坏的行为 | 把 `SimpleLazyObject(User)` 与 `SimpleLazyObject(user.pk)` 当成同一问题 |
| 定位与因果假设 | 20 | 0：纯猜测；25：只搜索关键字；50：找到字段/lookup 代码；75：沿调用链确认 `get_prep_value()`；100：通过基线实验排除历史提交、输出字段和关系字段等替代假设 | 只根据历史 commit 或文件名推断；没有复现前后差异 |
| 代码修改正确性与范围 | 25 | 0：未修改或明显无关；25：局部绕过；50：能使一个变体通过；75：在 ORM 边界正确解包并做模型 pk 归一化；100：行为通用、保持 Promise/关系字段语义、改动最小 | 修改无关类型；改变 `ForeignKey.get_col()` 等核心语义；使用私有 API 或异常吞掉真实错误 |
| 测试与验证 | 25 | 0：无验证；25：只做语法检查；50：自制冒烟脚本；75：原始复现通过；100：原始复现、边界案例、相关官方测试和结果/SQL 均通过 | 只测试 lazy pk；原始场景仍失败；测试数据为空却把“不报错”当成结果正确；管道隐藏失败码 |
| 错误恢复与工作流 | 10 | 0：遇错停止；25：重复尝试；50：能修复依赖/路径问题；75：能根据失败反馈修正假设；100：清理临时文件、恢复测试修改、保持可复现 | 大量 malformed shell 命令；误提交后未恢复；依赖安装噪声掩盖核心验证 |
| 最终收尾与证据 | 5 | 0：没有可交付补丁；25：有修改但未检查；50：生成 patch；75：检查 patch 范围；100：最终输出只包含预期源文件改动，并明确最后验证结果 | 没有最终补丁；patch 包含测试/配置改动；最后一次测试结果不清楚 |

这样设计的原因是：该任务的难点不在“能否编辑一个文件”，而在于是否定位到正确的 ORM 边界，并证明没有把一个特例变成全局行为。验证权重与实现权重合计 50 分，可防止“看似合理的补丁 + 局部成功脚本”获得过高评价。

## 具体案例

### 成功案例：`20260901_mini-v2.4.2_gemini-3-5-flash.json`

该轨迹的证据链最完整。

- `step 10` 检查了引入回归的提交 `354312982...`，并阅读 `Subquery`、`Query` 和字段实现。
- `step 36` 添加了与题目结构相近的嵌套子查询测试。
- `steps 62-74` 中，测试仍报告 `int() ... not 'Employee'` 或结果断言失败，说明模型没有把首次失败误认为成功。
- `step 76` 修正了测试数据，使内层子查询具有确定的单行语义。
- `step 77` 原始回归测试通过。
- `steps 78-80` 分别运行 expressions、queries 等相关测试；`step 83` 的 utils 测试也通过。
- 最终补丁只保留 `django/db/models/fields/__init__.py` 与 `related.py` 的源代码修改，包含 LazyObject 解包和模型主键处理。

这些事件支持“先复现、根据失败修正、再做交叉验证”的高质量过程。不过补丁使用 `__reduce__()` 和全局 Model-to-pk 处理，仍有一定实现风险；`resolved=true` 只能说明外部评测接受了结果，不能证明所有潜在语义都已覆盖。

### 失败案例：`20260217_mini-v2.0.0_claude-4-5-sonnet-high.json`

该轨迹过程上有不少有效探索，但最终证据与题目场景不一致。

- 轨迹修改了 `Field.get_prep_value()`、`related_lookups.py` 和 `Query.check_related_objects()`，试图统一解包 `LazyObject`。
- `step 75` 的原始 `reproduce_issue.py` 仍然报告：`Field 'id' expected a number but got <User: testuser>`。
- 随后 `step 95` 的 `test_final.py` 报告成功，但其嵌套测试主要使用 `SimpleLazyObject` 包装 pk，而不是题目中的 `SimpleLazyObject(lambda: User.objects.create_user(...))`。
- `step 92` 的 68 个关系相关测试通过，只能证明关系字段路径没有明显破坏，不能证明非关系 `AutoField` 输出的原始场景已修复。
- 最终 `resolved=false` 与“原始复现仍失败”相互印证。

该案例说明：局部测试通过不等于任务完成，尤其不能用更容易通过的 lazy-pk 变体替代原始 lazy-model 场景。

### 另一个失败案例：`20260217_mini-v2.0.0_claude-4-5-opus-high.json`

- 轨迹研究了历史提交，并最终只向 `django/utils/functional.py` 添加 `__int__`/`__float__` 代理。
- `step 44` 的局部数值测试通过，证明 lazy 包装整数时可以转换。
- 但 `step 45` 的完整题目复现仍失败，错误变为 `Field 'id' expected a number but got <SimpleLazyObject: <User: testuser>>`。
- 最终只运行了 LazyObject 相关单元测试；这些测试无法覆盖“lazy User 被用作整数注解 RHS”的场景。

根因是：给代理增加 `__int__` 并不能把 `User` 自动转换为 `User.pk`，因此修复了错误消息/局部行为，却没有修复任务语义。

## 逐条轨迹诊断

下表中的“过程分”是按上述细则估计的独立质量分；“resolved”严格采用用户提供的外部标签。

| 轨迹文件 | 过程分 | resolved | 过程质量诊断 |
|---|---:|:---:|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | 53 | false | 历史代码分析较深入，但最终只加数值代理；`step 45` 原始复现仍失败，验证与实现不匹配 |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | 67 | false | 找到了多个 ORM 入口并通过部分关系测试，但 `step 75` 原始场景仍失败，`step 95` 用 lazy pk 变体产生假阳性 |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | 41 | false | 能识别 `AutoField` 输出字段和 `int(User)` 问题，但反复切换历史补丁，`steps 87/91` 仍失败，未形成最终补丁 |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | 79 | true | 先修复依赖与测试环境，最终 `step 91` 完整复现通过；缺少官方回归套件，补丁范围略宽 |
| `20260217_mini-v2.0.0_glm-5-high.json` | 72 | true | `step 135` 原始查询不再报错，`test_promises` 24 项通过；修改 Decimal/Float/Integer 的模型转换过宽，部分测试命令配置错误 |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | 78 | true | 基线和修复后查询构造均有证据，`step 52` SQL 成功；只做 `py_compile` 和脚本验证，缺少官方测试与实际结果断言 |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | 47 | false | 初始定位方向基本合理，但 `apply_patch` 不可用、误提交后 reset，最终没有可靠的修复后原始复现证据 |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | 87 | true | 将处理集中在 `Lookup.get_prep_lookup()`，`step 75` 原始查询成功；`steps 128/129` 分别通过 464、262 项相关测试 |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | 87 | true | 与 Kimi 类似，`steps 97/102` 原始复现成功，查询/lookup/expressions 等大范围测试通过；中途有错误的边界脚本和命令 |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | 68 | false | 修改 `ForeignKey.get_col()` 后 `step 63` 复现通过，且 179/383 项测试通过，但改变输出字段语义的风险较高，外部评测未接受 |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | 91 | true | 有最完整的失败反馈、测试数据修正和官方套件验证；实现仍使用私有 LazyObject 细节，需额外审查长期兼容性 |

从这些分数看，成功组平均过程分约 82，失败组约 55，但样本只有 11 条，且模型版本、提示模板、依赖环境和执行时间不同，不能据此宣称模型能力存在统计显著差异。

## 可执行的改进建议

1. 固定一个验收脚本，必须原样使用题目中的 `SimpleLazyObject(lambda: User.objects.create_user(...))`，同时另测 lazy pk，禁止二者互相替代。
2. 在修改前后打印 `Subquery.output_field`、`type(rhs)`、`rhs._wrapped` 和最终 SQL，确认问题发生在 RHS 准备而不是 SQL 编译。
3. 优先在 ORM 的 lookup/field 准备边界做最小修改：先解包 LazyObject，再按关系字段或模型对象规则提取 pk；避免无条件改变所有数字字段。
4. 对每个补丁运行三类测试：原始回归、普通模型实例/普通 pk、Promise/列表/`__in`/`None` 等相邻行为。
5. 不使用 `cmd | head` 或 `cmd | tail` 判断测试成功；应保留完整退出码，或使用 `pipefail`。
6. 测试数据必须保证子查询确实返回目标行。查询“不报错但结果为空”只能证明构造/执行路径通过，不能证明业务结果正确。
7. 最终报告应列出最后一次原始复现、最后一次官方测试和实际 patch 文件，避免把中间实验结果当成最终结果。

## 不能仅凭这些轨迹确定的结论

- 不能确定某个模型“本质上更擅长 Django”；轨迹同时混入了不同模型版本、high 配置、提示模板、依赖状态和执行时长。
- 不能确定外部 `resolved` 使用了哪些隐藏测试，也不能从标签反推出唯一的 gold patch。
- 不能证明所有 `resolved=true` 补丁都没有隐藏回归；尤其是 GLM 的多字段模型转换、Gemini 3.5 的私有 API 使用，以及 Gemini Pro 的 `ForeignKey.get_col()` 改动。
- 不能仅凭“查询成功”断言数据库结果正确；多条轨迹没有创建完整的 A/B/C 关联数据，结果为空可能只是测试夹具不足。
- 不能把 shell 返回码 0 直接视为测试通过，因为多条轨迹使用了截断管道，且有测试框架错误被包装在输出中。
