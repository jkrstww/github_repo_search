# Django-16938 轨迹评测报告

## 1. 总体结论

样本共 11 条轨迹：7 条 `resolved=true`，4 条 `resolved=false`。问题根因是：

- M2M 序列化的非自然键路径使用 `related_manager.only("pk")`。
- 自定义默认管理器在 `get_queryset()` 中预先加入 `select_related("master")`。
- `only()` 会延迟字段，而 `select_related()` 又要求遍历该字段，最终触发 `FieldError`。
- JSON、JSONL、YAML 复用 `django/core/serializers/python.py`；XML 使用独立的 `xml_serializer.py`。因此只修 Python 路径是不完整的。

成功轨迹最稳定的共同特征是：

1. 能从 `handle_m2m_field()` 定位到 `.only("pk")`。
2. 明确理解 `select_related(None)` 是清除已有关联选择的正确 API。
3. 同时修改 Python 和 XML 序列化器。
4. 先复现原始 `FieldError`，再验证修复后的 JSON/XML 序列化。
5. 运行完整 serializers 测试，并清理临时测试文件和无关修改。

失败轨迹的主要原因：

- Claude Sonnet、Claude Opus 4.6、GPT-5-mini 最终只修改了 Python 序列化器，遗漏 XML。
- GPT-5-mini 采用 `values_list("pk", flat=True)` 的替代实现，但没有运行有效回归测试。
- Gemini 3 Pro 轨迹显示已修改两个文件且复现、测试均成功，但外部标签仍为 `false`，是本样本的异常点，不能仅凭轨迹确定失败原因。

因此，`resolved` 与单一步骤正确性不是等价关系。轨迹证据表明，“覆盖两个序列化实现并完成跨格式验证”与成功高度相关，但样本不足以证明模型名称、上下文长度或步数本身造成结果差异。

证据强度排序如下：

- 强证据：命令返回的原始 `FieldError`、修复后 JSON/XML 输出、完整测试结果、最终 `git diff`。
- 中等证据：模型对源码和调用链的解释、静态 grep 结果。
- 弱证据：仅有“应该成功”的文字判断、被管道截断的测试输出、没有观测结果的最终提交命令。

## 2. 可复用评分细则（总分 100）

| 维度 | 权重 | 高档表现 | 中档表现 | 低档表现 | 主要扣分条件与可观察证据 |
|---|---:|---|---|---|---|
| 任务理解与根因判断 | 20 | 准确说明 M2M、默认管理器、`select_related`、`only` 和 deferred 冲突，并区分自然键路径 | 找到冲突但未说明影响范围 | 只描述异常表象或误判 ORM 行为 | 误把 `resolved` 当作过程正确性；忽略自然键/预取分支 |
| 定位与假设验证 | 15 | 阅读 Python/XML 两处实现，检查 `QuerySet.select_related(None)`，成功构造最小复现 | 阅读主要源码并进行部分验证 | 主要依赖猜测或广泛搜索 | 未追踪到实际调用链；只静态修改不复现 |
| 代码修改正确性与范围 | 25 | 两个序列化器均修复，保留自然键和预取逻辑，改动最小且 API 语义正确 | 只修复一个路径，或采用等价方案但验证不足 | 修改错误、破坏返回类型或有无关改动 | 漏改 XML 扣 10 分；替代实现无测试扣 5 分；改变非目标逻辑再扣分 |
| 测试策略与验证证据 | 20 | 复现失败、验证修复、覆盖 JSON/XML，并运行完整相关测试和边界场景 | 有复现和部分测试，或只运行完整基线测试 | 未运行修复后测试，或测试与目标无关 | 只测 JSON 扣分；测试被 `tail`/`|| true` 掩盖失败时降低证据等级 |
| 错误恢复与工具纪律 | 10 | 能处理依赖、路径、测试发现等问题，并继续完成任务 | 有明显绕路但能恢复 | 重复失败、无法建立有效环境或放弃验证 | 多次错误命令、未解释的环境切换、临时文件未清理 |
| 最终收尾与可复现性 | 10 | `git diff` 仅含目标源码，状态清晰，补丁完整，提交前完成最终验证 | 补丁基本正确但收尾证据不完整 | 未检查 diff、混入测试改动或没有可提交结果 | 未确认 XML 是否包含在补丁；最终命令没有可观测输出 |

这样设计的原因是：实现覆盖和测试证据直接决定修复是否可用，合计 45 分；根因理解和定位决定是否能找到正确改动，合计 35 分；错误恢复与收尾反映工程执行质量，合计 20 分。该权重避免“解释得很好但漏改文件”获得过高评价。

## 3. 具体案例

### 成功案例：Claude Opus 4.5

文件：`20260217_mini-v2.0.0_claude-4-5-opus-high.json`，`resolved=true`。

关键事件：

- 第 7 步成功复现原始异常：`Field TestTag.master cannot be both deferred and traversed using select_related at the same time`。
- 第 8 步检查 `QuerySet.select_related()`，确认 `select_related(None)` 会清空选择。
- 第 12 步同时修改 Python 和 XML：
  `getattr(obj, field.name).select_related(None).only("pk").iterator()`。
- 第 14、15 步分别验证 JSON 和 XML 成功。
- 第 17 步运行 serializers 测试，186 项通过。
- 第 18 步继续运行 `select_related`、`defer`、`managers_regress` 等相关测试。
- 第 21 步验证预取和普通管理器边界情况。
- 第 23 至 25 步生成、检查并提交只包含两个目标源码文件的补丁。

这些证据覆盖了“复现根因、双路径修复、跨格式验证、回归测试、边界测试和最终收尾”，属于本样本中最完整的成功轨迹。

### 失败案例：Claude Sonnet 4.5

文件：`20260217_mini-v2.0.0_claude-4-5-sonnet-high.json`，`resolved=false`。

关键事件：

- 第 9 至 13 步多次修复复现脚本和环境问题，最终能够看到原始异常。
- 第 22、23 步只生成并应用了 Python 序列化器的补丁。
- 第 44、45 步添加了自定义 M2M 测试模型和测试文件，但第 46 步测试失败。
- 第 47、48 步重新应用 Python 修复后，自定义 JSON 测试通过。
- 第 49 步完整 serializers 测试显示 188 项通过，但此时临时测试模型仍在环境中。
- 第 51 步恢复测试模型；第 56 步得到 186 项基线测试通过。
- 第 60 至 64 步的最终状态明确显示只修改 `django/core/serializers/python.py`，最终补丁也只有该文件。

过程上它最终验证了 Python/JSON 路径，但没有保留或验证 XML 路径。由于 XML 序列化器仍使用 `.only("pk")`，该修复不能覆盖完整问题范围。这解释了 `resolved=false`，也说明“完整基线测试通过”不等于回归问题已覆盖。

### 异常案例：Gemini 3 Pro

文件：`20260226_mini-v2.0.0_gemini-3-pro-high.json`，`resolved=false`。

轨迹显示：

- 多次尝试后成功构造复现环境。
- 第 36 步修改 XML，第 23 步之前已修改 Python。
- 第 37、39 步 JSON/XML 均显示成功。
- 第 42 步运行 serializers 测试，输出显示 186 项通过。
- 第 40、46 步的 diff/补丁包含两个目标文件。

过程证据与其他成功轨迹接近，但外部结果仍为 `false`。可能原因包括评测环境与轨迹环境不同、补丁提取或提交状态问题、未覆盖评测器的隐藏测试，或外部标签噪声。仅凭该 JSON 无法判定具体原因。

## 4. 各轨迹诊断摘要

| 文件 | 模型 | 过程质量分 | resolved | 诊断摘要 |
|---|---|---:|:---:|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | Claude Opus 4.5 | 97 | true | 根因定位准确；复现 JSON/XML；双文件修复；完整 serializers、相关 ORM 和边界测试均通过；收尾完整。 |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | Claude Sonnet 4.5 | 71 | false | 探索较深入，也修复了 Python 路径，但最终遗漏 XML；临时回归测试过程混乱，最终基线测试未覆盖 XML 缺陷。 |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | Claude Opus 4.6 | 73 | false | 能复现并纠正 `select_related(False)` 到 `select_related(None)` 的错误；只改 Python，虽有 186 项基线测试通过，但未覆盖 XML。 |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | Gemini 3 Flash | 88 | true | 初期环境和测试脚本失败较多；最终双文件修复，验证两种调用顺序、JSON/XML、专门测试和完整 serializers。 |
| `20260217_mini-v2.0.0_glm-5-high.json` | GLM-5 | 93 | true | 阅读历史提交 #33937，成功复现；双文件最小修复；添加专门测试并运行 JSON、XML、完整套件；过程虽较冗长但证据充分。 |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | GPT-5.2 | 84 | true | `rg` 和 `apply_patch` 不可用后改用 grep/Python；双文件修复并成功完成临时 JSON/XML 复现和编译检查，但未运行完整测试套件。 |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | GPT-5-mini | 49 | false | 首次 `git apply` 失败后改为 Python 路径的 `values_list("pk")` 替代实现；提交了变更，但没有有效复现或回归测试，也遗漏 XML。 |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | Kimi K2.5 | 90 | true | 多次依赖、模型注册和测试入口失败；最终理解历史优化，双文件修复，完成 JSON/XML、自然键、边界和 186 项测试。 |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | MiniMax M2.5 | 91 | true | 先查 #33937，再复现原始异常；双文件修复；验证自然键、预取、through 模型和完整 serializers；有若干无效命令但恢复良好。 |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | Gemini 3 Pro | 86 | false | 轨迹证据显示双文件修复及 JSON/XML、完整测试均通过，但外部标签相反，是无法由轨迹解释的异常样本。 |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | Gemini 3.5 Flash | 90 | true | 先查历史提交和 ORM API；经历测试模型注册问题后，双文件修复、专门测试、JSON/XML 与完整 186 项测试均通过。 |

## 5. 可执行改进建议

1. 先建立最小复现，再修改代码；复现脚本必须明确创建数据库表，并在修复前后运行同一场景。
2. 对继承关系做影响分析：修改 Python 序列化器后，应立即检查 XML 等独立实现，而不能假设所有格式都继承同一基类。
3. 将回归测试参数化到 JSON、XML、JSONL、YAML，至少覆盖自定义 `select_related` 管理器和普通管理器。
4. 修复后必须运行目标测试和完整 serializers 测试；不要只依赖原有基线测试，因为原有测试可能不包含新回归场景。
5. 对 `values_list()` 等替代方案，必须验证返回类型、自然键分支、预取缓存分支和序列化输出格式。
6. 避免使用会掩盖退出码的命令，例如 `command | tail`、`|| true`；应保留明确的失败状态。
7. 最终检查 `git status` 和 `git diff -- <目标文件>`，确认测试模型、临时脚本和配置文件没有进入补丁。
8. 在评测记录中记录测试命令、测试数量、退出码和关键输出，减少外部 `resolved` 与过程证据不一致时的排查成本。

## 6. 不能仅凭这些轨迹确定的结论

- 不能据此断言某个模型家族必然优于另一个模型；样本量小，且模型版本、日期、提示模板、工具可用性和环境状态同时变化。
- 不能证明步数越多越好。GLM-5、Kimi 的轨迹较长但成功，Sonnet 同样较长却遗漏 XML。
- 不能仅凭 `resolved=false` 断定代码一定错误；Gemini 3 Pro 的过程证据与标签冲突。
- 不能确定外部评测失败究竟来自隐藏测试、补丁提取、提交状态、环境差异还是评测噪声。
- 不能从这些轨迹推断性能优化是否在所有数据库后端、复杂 through 模型或更深层 `select_related` 图上都保持不变；这些需要额外的后端和边界测试。
