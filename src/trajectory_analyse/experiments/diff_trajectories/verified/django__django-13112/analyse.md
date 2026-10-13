# Django #13112 多模型轨迹评测报告

## 一、评测范围与方法

本报告逐条检查了目录中的 11 个 ATIF JSON 轨迹，共 23–126 步/轨迹，覆盖任务理解、代码检索、根因假设、修改内容、测试输出、错误恢复以及最终补丁提交。`resolved` 仅作为外部结果单独列示，不直接计入过程质量分。

该问题的关键因果链是：

1. `ForeignObject.deconstruct()` 遇到字符串关系时执行 `self.remote_field.model.lower()`。
2. 它错误地把整个 `app_label.ModelName` 转为小写。
3. `DJ_RegLogin.Category` 因而被序列化为 `dj_reglogin.category`。
4. `StateApps` 使用大小写保留的 `DJ_RegLogin` 注册模型，待处理关系键却是 `dj_reglogin`，二者无法匹配。
5. 正确不变量应是：保留 app label，只将 model name 规范化为小写，即 `DJ_RegLogin.Category -> DJ_RegLogin.category`；同时仍须支持 `"self"` 和 `"Category"` 等无点号引用。

## 二、总体结论

外部结果为 **8 成功、3 失败**。其中 7 条成功轨迹直接修复了错误值的产生端 `ForeignObject.deconstruct()`；它们通常能够明确区分“app label 大小写敏感”和“model name 大小写不敏感”，并覆盖无点号引用。剩余一条成功轨迹，即 20260901 Gemini 3.5 Flash，通过让 registry 和 pending operation 全面执行大小写不敏感匹配来绕过问题，虽通过外部评测，但设计风险明显更高。

失败轨迹并非都缺少探索：

- GPT-5-mini 很快结束在错误层级，只验证了 `get_app_config()` 的大小写容错，没有验证真正失败的 pending operation 是否被消费。
- Gemini 3 Flash 定位正确、复现充分，但最终实现对所有字符串无条件调用 `make_model_tuple()`，破坏了 `"A"`、`"self"` 等合法无点号输入。
- Gemini 3 Pro 修复了表面报错，却取消了 model name 的小写规范化，违背迁移序列化的既有契约。

因此，成功与失败最稳定的分界不是步数、成本或是否运行过测试，而是：

| 分界因素 | 成功轨迹的典型表现 | 失败轨迹的典型表现 |
|---|---|---|
| 根因层级 | 修复产生错误引用的 deconstruct 路径 | 在 registry 消费端容错，或只修表面输出 |
| 不变量 | 保留 app label、仅小写 model name | 全部不小写、全部交给只接受点号格式的解析器 |
| 边界输入 | 检查 fully-qualified、bare name、`self` | 只验证原始 mixed-case 示例 |
| 测试闭环 | 原始错误 red→green，加 state/deconstruction/autodetector 回归 | 微型函数测试或单一 makemigrations 成功 |
| 错误恢复 | 从失败测试中修正实现，再重跑相关套件 | 未运行能够暴露最终缺陷的测试 |

证据强度方面，补丁 diff、原始异常、修复后的 `makemigrations`/`migrate` 输出和明确的 `Ran … OK` 属于强证据；根据补丁语义推断隐藏测试失败属于中等证据。由于没有隐藏评测日志或官方参考补丁，无法百分之百确认每条失败轨迹的唯一失败用例。

## 三、可复用的 100 分轨迹评分细则

| 维度 | 权重 | 高分档 | 中档 | 低分档及扣分条件 | 可观察证据 |
|---|---:|---|---|---|---|
| 任务理解与验收条件 | 10 | 9–10：准确提炼行为和兼容性要求 | 5–8：理解主症状但缺边界 | 0–4：把报错位置当根因；误解大小写契约 | reasoning、计划、预期输入输出 |
| 根因定位与因果验证 | 18 | 15–18：追到错误值产生端并解释传播链 | 8–14：位置基本正确但因果不完整 | 0–7：盲改消费端；未核对调用链 | 搜索路径、源码阅读、调试输出、red repro |
| 最终修改的语义正确性 | 27 | 23–27：满足主场景和合法边界输入 | 13–22：主场景正确但有明显兼容性缺口 | 0–12：核心问题仍存在或引入确定性回归 | 最终 diff、API 不变量、边界行为 |
| 修改范围与代码质量 | 12 | 10–12：最小、符合现有抽象 | 6–9：可工作但冗余或扩大语义 | 0–5：全局放宽契约、循环依赖或无关修改 | diff 范围、依赖方向、复杂度 |
| 验证充分性 | 20 | 17–20：原始 red→green、定向回归、边界和较宽套件 | 9–16：有主场景和部分回归 | 0–8：仅微型测试、无修前证据、未测试最终状态 | 命令、退出码、`Ran/OK`、生成迁移内容 |
| 错误恢复与工具纪律 | 8 | 7–8：识别失败原因并修正、复测 | 4–6：最终恢复但过程反复或判断不严 | 0–3：忽略错误、把被管道掩盖的失败称为成功 | 连续步骤、失败后的动作、环境修复 |
| 收尾与提交卫生 | 5 | 5：清理临时物、核对 status/diff、补丁范围正确 | 3–4：补丁正确但核验较弱 | 0–2：残留测试/调试修改或提交内容不明 | `git status`、最终 patch、清理命令 |

总分档位：90–100 为稳健；75–89 为良好但有局限；60–74 为部分可靠；低于 60 为高风险。

额外评分规则：

- 没有复现原始失败，验证维度原则上不超过 12/20。
- 最终实现破坏已知合法输入，语义正确性原则上不超过 16/27。
- `cmd | head/tail/grep` 未启用 `pipefail` 时，退出码可能属于管道末端；若没有明确 `Ran … OK`，验证扣 1–3 分。
- 临时修改测试后完整回滚不扣分；进入最终 patch 则按范围和收尾重复扣分。
- 同一个根因不重复惩罚；`resolved` 不加分、不扣分，以防结果泄漏替代过程评价。

该权重将“最终语义正确性 + 验证”置于 47%，因为长 reasoning、更多命令或漂亮收尾都不能补偿错误补丁；同时保留 28% 给任务理解和定位，以区分偶然通过与可复用的问题求解能力。

## 四、具体案例

### 案例 1：Claude 4.5 Opus，成功且证据链完整

文件：`20260217_mini-v2.0.0_claude-4-5-opus-high.json`

- 第 26 步真实复现原始异常：`DJ_RegLogin.Content.category` 指向 `dj_reglogin.category`。
- 第 38–40 步明确指出 `ForeignObject.deconstruct()` 把 app label 一并小写，并改为按最后一个点拆分，仅小写 model name；无点号时维持原来的 `.lower()` 行为。
- 第 41 步 `makemigrations` 成功，生成迁移中的引用为 `DJ_RegLogin.category`；第 43 步 `migrate` 成功。
- 第 47–52 步又运行 field deconstruction、migration state、autodetector 和 relative-field 测试，分别出现明确的通过摘要。
- 第 54 步最终 diff 只有 `django/db/models/fields/related.py`。

这是强证据支持的成功：定位、实现和验证都对应同一因果链，而不是仅依赖 `resolved=true`。

### 案例 2：GPT-5-mini，错误层级导致失败

文件：`20260217_mini-v2.0.0_gpt-5-mini.json`

- 前期找到了 `_pending_operations`、`make_model_tuple()` 和 `related.py`，但读取 `related.py` 时只看了前 260 行，没有看到第 585 行的关键 `.lower()`。
- 第 16–19 步直接假设 `get_app_config()` 应大小写不敏感，并只修改该方法。
- 第 21 步仅验证 `get_app_config('dj_reglogin')` 能返回 `DJ_RegLogin`，这不是原始 `makemigrations` 工作流。
- pending operation 仍以 `('dj_reglogin', 'category')` 保存，而 `do_pending_operations()` 仍按 `('DJ_RegLogin', 'category')` 精确弹出；修改最多改变错误文案，不能消费待处理关系。
- 没有原始 red→green，也没有 Django 回归套件。

因此 `resolved=false` 与可见过程高度一致；关键问题不是测试数量少本身，而是测试验证了错误的命题。

### 案例 3：Gemini 3 Flash，近似正确但漏掉合法输入

文件：`20260217_mini-v2.0.0_gemini-3-flash-high.json`

最终补丁把 `.lower()` 改为 `'.'.join(make_model_tuple(self.remote_field.model))`。这能正确处理 `DJ_RegLogin.Category`，第 77、82、85 步也证明原始复现可通过；但 `make_model_tuple()` 要求字符串包含恰好一个点，故 `"A"` 或 `"self"` 会抛 `ValueError`。

这一推断还有独立交叉证据：Minimax 轨迹曾采用同样的无条件解析方案，并在 `migrations.test_state` 中实际得到两个 `Invalid model reference 'A'` 错误，随后增加 `if '.' in ...` 分支才恢复通过。Gemini 3 Flash 最终没有运行这组 state 测试。故其 `resolved=false` 很可能来自该兼容性回归，但没有隐藏测试日志，仍应表述为高可信推断而非已证实事实。

### 案例 4：Gemini 3.5 Flash，外部成功但过程质量一般

文件：`20260901_mini-v2.4.2_gemini-3-5-flash.json`

该轨迹没有修复 deconstruct 产生错误引用的源头，而是同时修改 `get_app_config()`、`get_registered_model()`、`do_pending_operations()` 和 `make_model_tuple()`，使 app label 全局按大小写不敏感方式匹配。它运行了 788 个 migrations/invalid-model 测试并构造了新测试，外部结果也是 `resolved=true`。

然而这一方案扩大了 registry 的公共语义，可能使仅大小写不同的 app label 产生依赖迭代顺序的匹配，并在低层 `models.utils` 中引入对全局 app registry 的运行时依赖。它说明 `resolved=true` 证明了评测场景可通过，却不等于补丁范围和架构选择最优。

## 五、逐轨迹诊断

| 轨迹 | 过程分 | 过程质量诊断 | 外部结果 |
|---|---:|---|---|
| `claude-4-5-opus-high` | 96 | 精确 red→green；最小修复；覆盖 migrate、state、deconstruction、autodetector 和边界输入 | `true` |
| `claude-4-5-sonnet-high` | 93 | 正确识别双重 deconstruct 和大小写不变量；运行 35、123、552 等相关测试；少量环境试错不影响结论 | `true` |
| `claude-4-6-opus` | 95 | 根因分析深入；保留 bare/self 行为；552 个 migration 与 271 个相关测试通过，最终单文件补丁 | `true` |
| `gemini-3-flash-high` | 69 | 定位和原始复现较强，但最终无条件调用 `make_model_tuple()`，漏测无点号字符串并引入确定性回归 | `false` |
| `glm-5-high` | 88 | 能从初版测试失败中恢复，并运行 migrations、model_fields、check_framework 等宽回归；但额外修改 migrations operation utility，范围偏大 | `true` |
| `gpt-5-2-high` | 87 | 根因和 mixed-case 实际工作流验证充分，且考虑 `self`、bare string、SettingsReference；标准回归套件证据相对不足，实现略重 | `true` |
| `gpt-5-mini` | 37 | 找到相关模块却未读取关键代码；修改错误层级；只验证 registry 微行为，无原始复现和回归测试 | `false` |
| `kimi-k2-5-high` | 88 | 最终语义正确且测试较广；实现先整体小写再从原字符串恢复 app label，略显迂回，但不影响核心行为 | `true` |
| `minimax-2-5-high` | 93 | 初版确实因 `"A"` 失败，随后准确增加有点/无点分支并重跑 state、deconstruction、autodetector、全 migrations | `true` |
| `gemini-3-pro-high` | 58 | 原始复现和主场景修后通过，但完全保留字符串 model name、类引用使用 `object_name`，破坏小写规范；没有正式回归套件 | `false` |
| `gemini-3-5-flash` | 72 | 测试数量充足且外部通过，但采取全局大小写容错，扩大 registry 语义并引入不必要依赖，过程正确性低于结果表现 | `true` |

步数和成本与成功没有单调关系：95 步的 Gemini 3 Flash 失败，75 步且成本较低的 Minimax 成功，23 步的 GPT-5-mini 失败。样本更支持“因果假设和边界测试决定结果”，不支持“更多步骤或更高成本自然更可靠”。

## 六、可执行改进建议

1. 在修改前写出不变量表：`DJ_RegLogin.Category -> DJ_RegLogin.category`、`Category -> category`、`self -> self`、模型类目标使用 `_meta.label_lower`。
2. 必须复现完整 `makemigrations` 异常，而非仅测试 `get_app_config()`；修后再运行同一命令形成 red→green。
3. 优先修复错误值产生端。除非需求明确要求 registry 大小写不敏感，否则不改变全局查找和 pending-operation 匹配语义。
4. 最低测试矩阵应包括 mixed-case 完整引用、bare model、`self`、模型类、swappable reference，以及 `migrations.test_state`、`field_deconstruction`、`migrations.test_autodetector`。
5. 运行测试时避免用未启用 `pipefail` 的 `head`/`tail` 掩盖退出码；保留明确的 `Ran … OK/FAILED`。
6. 每次从失败测试修正实现后，重新运行原失败测试和完整相关套件，确保验证针对的是最终代码状态。
7. 最终检查 `git diff` 和 `git status`，确认临时项目、调试打印、测试修改和安装产物未进入补丁。
8. 对评测系统，应同时保存隐藏测试失败摘要；单个 `resolved` 位无法区分功能缺陷、补丁提取失败和基础设施问题。

## 七、无法仅凭这些轨迹确定的结论

- 无法确定 Gemini 3 Flash 和 Gemini 3 Pro 的具体隐藏失败用例；报告中的原因来自最终补丁语义及可见交叉证据。
- 无法证明某个模型家族本质上优于另一模型。每种设置只有一个样本，且提示、预算、日期、环境依赖和 mini-swe-agent 版本并不完全一致。
- 不能把 20260901 样本的成功归因于 mini-v2.4.2；它既更换了 agent 版本，也更换了模型和实现策略。
- `resolved=true` 不能证明补丁符合 Django 长期兼容性、性能和架构要求；尤其不能消除全局大小写容错方案的歧义风险。
- 轨迹中的部分命令经过管道或输出截断，且最终提交步骤的 observation 为空，因此只能依据此前展示的 patch 内容确认提交意图，不能独立验证评测端实际接收的字节完全一致。
