# 轨迹评测报告：django__django-16560

## 总体结论

共审阅 11 条 ATIF 轨迹，外部 `resolved=true` 为 3/11（27.3%）。任务实质是为 `BaseConstraint` 及 `CheckConstraint`、`UniqueConstraint`、PostgreSQL `ExclusionConstraint` 增加 `violation_error_code`，并在验证失败时传入 `ValidationError(code=...)`；同时需维护反序列化、相等性、表示、旧版唯一性错误分支及模型级错误归属。

成功轨迹的共同点是：定位范围覆盖基类、各子类和所有 `validate()` 抛错点；将新字段纳入构造、`deconstruct()`、`__eq__()`（通常还有 `__repr__()`）；至少运行 Django 的 `constraints` 测试集，并在提交前检查限定文件的补丁。`glm-5` 还补充了公开 API 文档。  
失败轨迹并非都“没有测试”：有的已有套件全绿，但漏掉 `ExclusionConstraint`、`UniqueConstraint(fields=...)` 的兼容分支，或擅自扩展 `Model.validate_constraints()` 的字段归属语义。说明通用回归测试不足以证明该 API 的分支行为正确。

证据强度为中等：补丁、命令、观察输出和提交过程是直接证据；`resolved` 是最终结果证据。但没有隐藏测试的失败日志，且部分命令使用 `| head`、`|| true`，使 shell 返回码不能可靠代表测试通过。因此，下面对“失败原因”均为由轨迹支持的高风险缺陷，而非对隐藏断言的唯一归因。单样本、不同时间和不同 agent 版本也不能用于推断模型能力排序。

## 可复用评分细则（100 分）

| 维度 | 分值 | 满分档 | 合格档 | 主要扣分条件与可观察证据 |
|---|---:|---|---|---|
| 任务理解与假设 | 15 | 明确区分“消息”和“错误码”，列出兼容性目标 | 知道新增参数，但未说明旧行为 | 将需求误写为消息参数、设定无依据默认码；推理与补丁冲突 |
| 定位与影响面 | 15 | 覆盖基类、三类约束、所有抛错路径、唯一性旧分支 | 覆盖主文件和主要子类 | 未查 PostgreSQL 约束、未查 `unique_error_message()`、遗漏 `deconstruct`/比较 |
| 修改正确性与最小性 | 25 | 参数、存储、迁移序列化、验证传播与兼容分支一致，改动局部 | 主路径可用但存在遗漏 | 漏传递、错误默认值、直接覆写 `.code`、无关改动 `base.py`、整文件覆写造成风险 |
| 验证质量 | 25 | 自定义码的直接验证与 `full_clean()` 均覆盖；检查默认码、fields/expressions/condition、既有套件 | 跑相关 Django 套件或可靠最小复现 | 仅 `py_compile`、测试未安装、`head` 掩盖失败、手写脚本未建表或断言不完整 |
| 工具选择与错误恢复 | 10 | 根据输出纠正环境、命令和补丁，重新验证 | 能恢复主要环境错误 | `cmd1 || cmd2` 导致后续检查未执行；`|| true` 吞错误；失败后未回归 |
| 收尾与交付卫生 | 10 | 仅提交目标源码/文档，检查 diff，说明验证边界 | 有补丁并检查主要文件 | 提交范围遗漏、临时文件未清理、擅自提交/依赖历史 commit、未检查最终 patch |

各维度按“满分档 90%-100%、合格档 60%-89%、不足档 0%-59%”折算。该设计把最高权重放在语义正确性和可证伪验证，而非命令数量：本任务最容易在 `UniqueConstraint` 的特殊分支和错误字典归属处出现“测试全绿但 API 不正确”的假阳性。

## 具体案例

### 成功案例：`20260217_mini-v2.0.0_minimax-2-5-high.json`

过程分约 85/100，外部结果为成功。轨迹先检查 `ValidationError` 和三个约束实现，随后分别修改基类、`CheckConstraint`、`UniqueConstraint` 与 `ExclusionConstraint`，并把新字段纳入比较和表示。第 32、33 步改用项目的 `tests/runtests.py constraints`，第 49、54 步明确记录 73 项测试通过。

其强点不在于命令数量，而在于在最初 `pytest` 不存在、`django test` 路径不正确后，转向项目原生测试入口并取得可读的 `OK (skipped=4)` 证据。最终补丁只含两个目标源码文件。局限是 PostgreSQL 测试因环境跳过，且没有新增仓库测试来固定新 API。

### 失败案例：`20260217_mini-v2.0.0_claude-4-5-sonnet-high.json`

过程分约 54/100，外部结果为失败。该轨迹有较多恢复和验证：第 25 步整文件覆写 `constraints.py` 后，第 27 步出现 `SyntaxError`，随后从 `HEAD` 恢复并重建；第 50、51、60、61、62、66 步分别显示相关既有测试通过。

但最终提交证据显示补丁只包含 `django/db/models/constraints.py`，没有 `django/contrib/postgres/constraints.py`。这与任务的公共 `BaseConstraint` API 影响 `ExclusionConstraint` 的事实不一致，构成直接的影响面遗漏。它说明大量泛化回归测试和手写脚本不能弥补未纳入最终 patch 的子类支持。

## 逐轨迹诊断

| 轨迹文件 | 过程分 | 过程质量诊断 | `resolved` |
|---|---:|---|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | 58 | 覆盖三类约束并做手写验证；但未见 `__repr__()` 支持，`UniqueConstraint(fields=...)` 仍沿旧错误路径，测试多受管道和依赖问题影响。 | 失败 |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | 54 | 恢复能力和回归数量较好，但整文件覆写曾致语法错，最终漏交 PostgreSQL 约束支持。 | 失败 |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | 61 | 定位和子类覆盖较完整，73 项测试通过；但遗漏表示层，并未处理字段唯一约束的自定义码兼容语义。 | 失败 |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | 55 | 注意到 `unique_error_message()`，但以直接改写 `ValidationError.code` 实现，格式和分支语义风险较高；没有可靠项目测试通过证据。 | 失败 |
| `20260217_mini-v2.0.0_glm-5-high.json` | 84 | 前期工具参数多次失效，但后续补齐三类约束、文档、手写覆盖和 80 项相关回归测试；最终 patch 范围受控。 | 成功 |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | 70 | 影响面判断较好，额外防护 `validate_constraints()` 对无 `fields` 约束的访问；但主要只做编译检查，未跑 Django 套件，过程证据偏弱。 | 成功 |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | 35 | 多次 `git commit` 与任务交付约束不符；`pytest` 未安装但被 `|| true` 吞掉，且最终依赖历史 commit 生成补丁。 | 失败 |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | 49 | 错把默认错误码设为 `"constraint_violation"`，与现有 `None` 语义冲突；自测中的 `deconstruct()` 已报错，未形成闭环。 | 失败 |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | 85 | 三类约束、存储/比较/表示和抛错路径均覆盖；在错误测试入口后恢复为项目测试，最终 73 项通过。 | 成功 |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | 55 | 最小复现验证了 Check/Unique 主路径，但只改主约束文件，漏掉 Exclusion；对旧唯一错误包装的语义也不稳。 | 失败 |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | 67 | 验证最充分之一，包含 `full_clean()` 和多个套件；但将自定义唯一错误码纳入 `Model.validate_constraints()` 的字段级归属，是超出需求且未证明兼容性的行为扩张。 | 失败 |

## 可执行改进建议

1. 修改前建立分支矩阵：Check、Unique-expression、Unique-fields、Unique-condition、Exclusion，以及默认码与自定义码各一例；明确旧字段唯一错误何时必须保留。
2. 在同一最小模型中同时断言直接 `constraint.validate()` 和 `full_clean()` 的 `error.code`、错误键和消息，避免只验证约束对象属性。
3. 测试命令禁止用 `| head`、`|| true` 作为最终通过证据；需要截断输出时保留原始退出码，例如用临时日志后再读取尾部。
4. API 新字段一律检查构造、`deconstruct()`、`clone()`、`__eq__()`、`__repr__()` 与所有子类构造器；公共 API 同步更新文档。
5. 对 `base.py` 等共享层改动设置更高门槛：先证明原实现会崩溃或错误，再添加覆盖其兼容性的回归测试；否则保持改动局部。
6. 收尾时以最终 `patch.txt` 为准逐文件核查，不以工作区或中间测试脚本为准。

不能仅凭这些轨迹确定的结论包括：各模型本身的稳定能力排名、隐藏测试的具体断言、外部失败是否由单一缺陷造成，以及某些文档或表示层修改是否为评测的硬性要求。
