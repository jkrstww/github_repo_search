# Django `#15916` 轨迹评测报告

## 总体结论

11 条轨迹中，外部 `resolved=true` 为 6 条，`false` 为 5 条。最强的区分信号不是模型名称，而是是否完整识别了两个相互关联的语义层：

1. `ModelFormMetaclass` 必须读取当前定义类的 `Meta.formfield_callback`，使直接定义的 `ModelForm` 生效。
2. `modelform_factory()` 在参数为 `None` 时不能向新类显式注入 `formfield_callback=None`，否则会覆盖基类/`Meta` 的回调继承。

三条失败轨迹 `claude-4-6-opus`、`kimi-k2-5-high`、`gemini-3-pro-high` 都只修复了第 2 点，且记录中已经出现“直接定义 `Meta.formfield_callback` 仍不生效”的反证。它们的定向测试和既有回归测试仍可通过，但未覆盖需求的完整契约。`gpt-5-mini` 的失败更直接：代码被 `git add` 暂存后，使用未暂存的 `git diff` 生成补丁，最终 `patch.txt` 为空。

成功轨迹普遍具备直接 `Meta`、工厂继承、显式工厂回调覆盖三类行为验证中的至少前两类；其中 `minimax-2-5-high` 与 `gemini-3-5-flash` 还运行了相关 Django 测试模块。成功不等于过程完全严谨，例如部分轨迹在最终提交前只运行了脚本或编译检查。

证据强度分层如下：

- 强：失败轨迹自己打印出直接 `Meta` 回调为 `False`，却提交仅改 factory 的补丁；`gpt-5-mini` 的空补丁可直接由命令链确认。
- 中：成功轨迹的行为断言、`model_forms`/`model_formsets` 通过输出，与外部 `resolved` 一致。
- 弱：若命令使用 `| head` 或 `| tail`，管道末端通常返回 0，不能以工具返回码证明前面的测试成功。多条记录中 `pytest` 实际报 “No module named pytest”，但整体命令仍显示返回码 0。

外部 `resolved` 是最终评测结果，不应倒推每一步都正确；本报告的过程评分不把 `resolved` 当作评分项。

## 可复用评分细则（100 分）

| 维度 | 权重 | 高分档 | 中分档 | 低分档与扣分条件 | 可观察证据 |
|---|---:|---|---|---|---|
| 任务与契约理解 | 15 | 13-15：明确区分“当前类 Meta 生效”与“factory 继承” | 7-12：只识别覆盖现象或隐含假设 | 0-6：把示例的类属性与 Meta 混为一谈；遗漏显式覆盖语义 | 推理文字、测试矩阵 |
| 定位与假设验证 | 15 | 13-15：同时检查 metaclass、factory、调用链 | 7-12：定位到单一函数但未验证机制 | 0-6：凭文件名/表面代码直接修改 | 搜索、局部阅读、反例 |
| 可执行复现 | 15 | 13-15：最小、独立、有断言，失败后修复可复跑 | 7-12：能打印现象但覆盖不全 | 0-6：环境失败后放弃，或假测试对象不兼容 | 退出码、断言、输出 |
| 实现正确性与范围 | 20 | 17-20：最小改动，覆盖两个层次，保留显式回调覆盖 | 9-16：局部有效但漏场景或引入多余语义 | 0-8：改错层、扩大 API 行为、提交空补丁 | 最终 diff、变更范围 |
| 验证质量 | 20 | 17-20：定向矩阵加相关回归；输出可证实通过 | 9-16：仅定向脚本或仅既有回归 | 0-8：测试未运行、被管道掩盖、断言与需求不符 | 测试命令与完整末尾输出 |
| 错误恢复与交付 | 15 | 13-15：修复环境/命令错误，清理临时文件，确认补丁内容 | 7-12：有恢复但最终状态证据不足 | 0-6：忽略失败、错误路径未恢复、补丁为空或未核验 | 后续命令、`git diff`、提交前检查 |

这样设计的原因是：该类任务的风险集中在“需求被窄化”和“测试假阳性”，因此将实现与验证合计设为 40 分；交付本身也占 15 分，以防正确工作区改动因补丁生成错误而完全失效。

## 具体案例

### 成功案例：`20260901_mini-v2.4.2_gemini-3-5-flash.json`

- 步骤 11、14、15 分别验证了：示例失败、类属性回调的继承异常、仅 `Meta` 回调时直接 `ModelForm` 不生效。
- 步骤 27 同时修改 metaclass：当 `attrs["Meta"]` 有 `formfield_callback` 时作为回调来源；并修改 factory：仅在参数非 `None` 时写入类属性。
- 步骤 29 验证直接 `Meta` 与 factory 均为 required；步骤 30 验证显式工厂回调可覆盖；步骤 31-32 运行 `model_forms`、`model_formsets`、`model_formsets_regress`。

这条记录的关键在于，它没有把“factory 生成的类从基类 Meta 继承回调”误认为“当前 ModelForm 的 Meta 已被支持”。这与 `resolved=true` 一致，证据强。

### 失败案例：`20260226_mini-v2.0.0_gemini-3-pro-high.json`

- 步骤 12 明确打印：`MyForm 'name' field required: False`，即当前类的 `Meta.formfield_callback` 根本没有生效。
- 步骤 23-24 只将 factory 的 `form_class_attrs` 改为在非 `None` 时加入回调；该改动使 factory 从继承的 `Meta` 中获得回调。
- 步骤 25-26 的附加测试只验证 factory 场景；步骤 27 的类属性测试失败，且没有形成相应修复。
- 最终 patch 仅包含 factory 修改，缺少 metaclass 对当前 `Meta` 的支持。

因此，它“修好了示例的一个表现”，但未完成标题所要求的 “Allow ModelForm meta to specify formfield_callback”。这与 `resolved=false` 高度一致。

## 逐轨迹诊断

| 轨迹文件 | 过程评分 | 过程质量诊断 | `resolved` |
|---|---:|---|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | 87 | 先修 factory，随后发现当前 `Meta` 自身未生效并补 metaclass；有定向脚本、边界脚本及相关回归。改动略显扩张，但验证扎实。 | true |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | 88 | 初始复现配置有误，后续区分了类属性与 Meta；最终覆盖直接 Meta、继承、覆盖和相关套件。个别 `pytest` 失败被管道返回码掩盖。 | true |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | 69 | 正确诊断 factory 的 `None` 覆盖，却用“类属性 + Meta”组合测试掩盖了当前 Meta 失效；最终仅改 factory。 | false |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | 84 | 反复试验后完整修复两层语义，并跑相关套件；中途错误插入代码后用 checkout 恢复，最终一次临时测试因清理 app 失败，收尾证据稍弱。 | true |
| `20260217_mini-v2.0.0_glm-5-high.json` | 60 | 复现和回归很多，但引入 `_NOT_PROVIDED` 哨兵并改动 `fields_for_model`、多个工厂的语义，偏离最小修复；把“显式 None 禁用继承”作为新契约，风险高。 | false |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | 79 | 清楚验证直接 Meta 失败，最终同时修 metaclass 和 factory，并检查显式覆盖；只做 `compileall`，缺少相关 Django 套件。 | true |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | 42 | 修改意图接近 factory 修复，但 `git add -A` 后使用 `git diff` 生成未暂存差异，`patch.txt` 为空；复现也因缺依赖失败后未恢复。 | false |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | 66 | 有大量自制测试与回归，但已观察到类属性回调在 factory 中失败，最终只从 `form.Meta` 回退；没有补当前 Meta 的 metaclass 支持。 | false |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | 90 | 明确发现直接 Meta 是独立问题，在 metaclass 中读取当前 Meta 并保存回调；定向、继承、覆盖、177/74/22/610 项相关测试均有输出。实现比最小方案更复杂。 | true |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | 70 | 复现中已记录直接 Meta 不生效，却只提交 factory 改动；既有测试通过不能覆盖新增需求。 | false |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | 89 | 先建立机制性反例，再同时修当前 Meta 与 factory 覆盖；直接行为、显式覆盖和相关套件均验证，交付前检查补丁。 | true |

## 可执行改进建议

1. 固化四格测试矩阵：直接 `ModelForm.Meta`、factory 默认继承、factory 显式回调覆盖、无回调默认行为。每格使用断言而不是仅打印。
2. 将机制检查前置：读取 `ModelFormMetaclass.__new__()` 与 `modelform_factory()` 后，写出“回调来源优先级”。这样可避免只改 factory。
3. 测试命令避免 `| head`、`| tail`；必须截断时启用 `set -o pipefail`，并保留测试结尾的 `Ran ... OK` 或失败堆栈。
4. 提交前运行 `git diff -- <source>`、检查 patch 非空且只含目标源文件；若已暂存则使用 `git diff --cached` 或避免暂存。
5. 对小修复限制变更面。除非有明确兼容性需求，不应为区分“省略参数”和“显式 None”而改动多个公开工厂的默认语义。
6. 将新回归测试加入正式 `tests/model_forms/tests.py`。临时脚本有诊断价值，但不能替代永久防回归测试。

## 不能仅凭轨迹确定的结论

- 不能断言 `glm-5-high` 的唯一失败原因就是哨兵方案；外部评测未提供失败断言，只能确认它扩大了语义和风险面。
- 不能比较模型的固有编码能力或泛化能力：样本仅一个 Django issue，环境依赖、命令偶然性和提交机制均会显著影响结果。
- 不能把既有测试模块通过视为新需求已被覆盖；这些模块在多个最终失败轨迹中同样通过。
