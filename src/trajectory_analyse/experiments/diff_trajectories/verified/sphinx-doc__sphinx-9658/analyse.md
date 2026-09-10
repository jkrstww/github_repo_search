# Sphinx-9658 轨迹评测报告

## 1. 总体结论

样本共 11 条轨迹，其中 8 条 `resolved=true`，3 条 `resolved=false`，成功率为 72.7%。

多数轨迹最终定位到同一根因：

- `ClassDocumenter` 在处理 `__orig_bases__` 时，拿到的可能不是 mock 类，而是由 `autodoc_mock_imports` 产生的 mock 实例。
- `_MockObject.__init__()` 将实例的 `__qualname__` 设为空字符串。
- `restify()` 按 `__module__ + "." + __qualname__` 格式化，因此产生 `torch.nn.`、`missing_module.` 这类缺失类名的结果。
- 生成的真实 mock 类本身通常仍具有正确的 `__qualname__`，所以只检查 `__bases__` 可能看不出问题；必须检查 `__orig_bases__`。

成功轨迹的共同特征是：

1. 先定位 `sphinx/ext/autodoc/mock.py`、`sphinx/ext/autodoc/__init__.py` 和 `sphinx/util/typing.py`。
2. 构造了继承 mock 类的最小复现。
3. 明确比较了 `__bases__` 与 `__orig_bases__` 的差异。
4. 采用局部修复，主要是将 mock 实例的 `__qualname__` 设置为其 mock 类名。
5. 至少运行了 mock 相关测试，并验证生成结果为 `torch.nn.Module`。

失败轨迹则有两类：

- GLM-5 轨迹明显陷入长时间试错，最终修改了通用类型格式化逻辑，并且观察到 `tests/test_util_typing.py` 的多个失败。
- Gemini Pro 和 Gemini 3.5 Flash 的轨迹在可见测试和手工构建中表现良好，但外部 `resolved=false`。这说明局部成功不等于评测成功，可能存在隐藏测试、补丁语义偏差或提交状态问题；仅凭轨迹无法确定具体原因。

证据强度从高到低大致为：

1. 复现 `__orig_bases__` 中的 mock 实例，并直接观察错误输出，再验证修复后的完整文档输出；
2. 运行针对 mock、autodoc 和类型格式化的测试；
3. 只运行内联脚本或只检查 `git diff`；
4. 只看到命令返回码为 0，但没有显示断言、测试数量或最终文档内容。

主要局限：

- `resolved` 是外部评测标签，轨迹中没有隐藏测试详情。
- 不同轨迹使用了不同日期、mini-swe-agent 版本和模型设置，不能把结果差异归因于模型能力本身。
- 部分轨迹安装依赖、修改环境或生成临时文件，运行环境并不完全一致。
- 一些测试失败可能来自依赖缺失或基线环境，而非补丁本身。

## 2. 可复用的 100 分轨迹评分细则

| 维度 | 权重 | 高档 | 中档 | 低档 | 可观察证据 |
|---|---:|---|---|---|---|
| 任务理解与验收标准 | 15 | 13–15：准确复述 mock 继承、`Bases` 输出和源码修改边界 | 8–12：知道问题方向，但验收条件不完整 | 0–7：误解问题或只围绕标签行动 | 是否明确要求 `torch.nn.Module` 而不是 `torch.nn.`，是否区分源码、测试和配置 |
| 根因定位与假设验证 | 20 | 17–20：定位到 `__orig_bases__`、mock 实例、空 `__qualname__`，并用实验验证 | 10–16：根因基本正确，但依赖猜测或缺少关键对照 | 0–9：只改格式化函数或反复试错，无因果证据 | `__bases__`/`__orig_bases__` 类型比较、`__module__`、`__name__`、`__qualname__`、`__display_name__` 输出 |
| 工具选择与复现纪律 | 15 | 13–15：最小复现、搜索范围合理、命令有明确目的 | 8–12：能复现，但脚本多次重写或依赖处理混乱 | 0–7：大量无关命令、无效命令或无复现 | grep/sed/git log、最小 Sphinx 项目、是否记录失败原因 |
| 实现正确性与改动范围 | 20 | 18–20：单点、向后兼容、覆盖实例与边界情况 | 10–17：基本有效但改动偏宽、重复逻辑或隐藏回归风险 | 0–9：改错层次、引入明显回归或没有真正修复 | 补丁是否只改必要源码；是否避免把 mock 特殊逻辑扩散到通用类型系统 |
| 测试与验证证据 | 20 | 18–20：复现输出、目标测试、相关回归测试均通过 | 10–17：有目标测试或构建验证，但覆盖不完整 | 0–9：测试缺失、失败被忽略或只看命令成功 | 测试数量、失败断言、`Bases:` 实际输出、HTML/text 构建结果 |
| 错误恢复与最终收尾 | 10 | 9–10：依赖失败后恢复，撤销调试改动，补丁内容准确且干净 | 5–8：能继续完成，但有临时文件、状态混乱或验证不足 | 0–4：留下失败状态、补丁不完整或无法确认提交内容 | 是否清理临时脚本、是否检查 `git status`、是否单独生成并核对 patch |

建议使用以下扣分规则：

- 每个未解释的测试失败：扣 3–5 分。
- 将通用逻辑改为 mock 专用逻辑但没有回归测试：扣 5–10 分。
- 只验证 `__bases__`、没有验证 `__orig_bases__`：扣 5 分。
- 依赖缺失导致测试未执行，却将结果描述为“全部通过”：扣 5–8 分。
- 临时调试代码未恢复、修改测试或配置、补丁包含无关文件：每项扣 3–8 分。
- 反复执行无效命令、长时间无新信息试错：扣 2–5 分。

这样设计的原因是：本题的难点不在于写一行代码，而在于识别 Python PEP 560 的运行时差异，并证明修复不会破坏普通类、泛型和 autodoc 其他路径。因此根因定位和验证各占 20 分，单纯“改对代码但没有证据”不能获得高分；同时将 `resolved` 与过程分开，避免外部标签掩盖轨迹质量。

## 3. 具体案例

### 成功案例：`20260217_mini-v2.0.0_claude-4-6-opus.json`

该轨迹过程紧凑，证据链完整：

- 先搜索 mock 实现、`Bases` 生成位置和 `restify()`。
- 通过脚本复现 mock 继承行为，确认 `__orig_bases__` 保存的是 mock 实例。
- 明确指出空 `__qualname__` 会导致 `restify()` 输出 `torch.nn.`。
- 将 [mock.py] 中的赋值从空字符串改为 `self.__class__.__name__`。
- 验证 `restify()` 对 `__orig_bases__` 和 `__bases__` 都输出 `:py:class:\`torch.nn.Module\``。
- 运行 `tests/test_ext_autodoc_mock.py`，7 个测试通过；运行 `tests/test_ext_autodoc_autoclass.py`，16 个测试通过。
- 最终生成的 patch 只包含一个必要源码文件。

这条轨迹的证据直接覆盖了问题根因、修复效果和回归风险，因此过程分可评为 95/100，且 `resolved=true`。

### 失败案例：`20260217_mini-v2.0.0_glm-5-high.json`

该轨迹虽然最终也理解了 `__orig_bases__` 中 mock 实例的问题，但过程质量明显较弱：

- 总计 179 个步骤，包含大量重复的 `printf`、无信息命令和逐步拼接测试脚本。
- 初始依赖缺失导致复现失败，之后虽安装依赖，但命令管理仍然混乱。
- 轨迹中反复检查 `__qualname__`、`ismock()` 和 MRO，花费大量步骤才确认根因。
- 最终没有采用最小的 `mock.py` 修复，而是在 `sphinx/util/typing.py` 中增加 `_is_mocked()` 并改变 `restify()` 分支。
- 修改后 `tests/test_ext_autodoc_mock.py` 通过，但 `tests/test_util_typing.py` 明确出现多个失败，包括 `restify(Any)` 和容器类型相关断言。
- 在存在这些失败的情况下仍生成并提交 patch。
- 外部结果为 `resolved=false`。

该轨迹说明“核心思路正确”并不足够：改动层次过宽、回归测试失败且未恢复，是失败的强证据。过程分约 43/100。

### 补充案例：`20260901_mini-v2.4.2_gemini-3-5-flash.json`

该轨迹值得单独关注，因为它展示了过程与外部结果的不一致：

- 进行了较完整的源码搜索、复现和 git 历史查询。
- 最终在 `restify()` 和 `stringify()` 中同时加入 mock 检测。
- 生成了正确的 `Bases: torch.nn.Module`，并通过 mock 测试及 91 个 autodoc 相关测试。
- 但补丁把 mock 判断扩散到通用类型系统，并使用 `issubclass(..., _MockObject)` 与 `__display_name__`，对普通用户类、继承 mock 的真实类和类型注解存在潜在语义风险。
- 外部标签为 `resolved=false`。

因此，测试通过只能证明可见场景有效，不能证明改动符合隐藏测试或项目原有抽象边界。

## 4. 逐条轨迹诊断

| 轨迹文件 | 模型 | 过程分 | 过程质量摘要 | `resolved` |
|---|---|---:|---|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | Claude Opus 4.5 | 88 | 能复现并定位空 `__qualname__`，一行修复，7 个 mock 测试通过；曾用覆盖式写文件，且 broader 测试只选中 0 项 | true |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | Claude Sonnet 4.5 | 82 | 调查最深入，检查了 `__orig_bases__` 和历史提交；临时调试较多，通用测试曾出现 `test_enum_class` 失败，最终 patch 改动到 `typing.py` | true |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | Claude Opus 4.6 | 95 | 复现、根因、最小修复、边界测试和回归测试均完整，步骤少且信息密度高 | true |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | Gemini 3 Flash | 79 | 通过多轮脚本确认问题，最终用 `__display_name__` 修复 `restify()`；中间有依赖和格式化错误，缺少正式完整测试证据 | true |
| `20260217_mini-v2.0.0_glm-5-high.json` | GLM-5 | 43 | 179 步且大量重复试错；最终改通用 `typing.py`，明确出现多个类型格式化测试失败 | false |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | GPT-5.2 | 93 | 解释了 `__orig_bases__` 的机制，使用局部 `mock.py` 修复，目标测试 7/7 通过；早期 patch 工具不可用但后续恢复 | true |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | GPT-5 Mini | 68 | 修改了 py36/py37 两套通用 `restify` 逻辑，补丁范围较宽；pytest 一度不可用，存在 staging 和 patch 生成混乱 | true |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | Kimi K2.5 | 78 | 调查较长，最终增加 `pass`、特殊属性处理和 `__qualname__`；可见测试后续通过，但实现复杂度高于必要范围 | true |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | MiniMax M2.5 | 91 | 快速复现并采用一行 `mock.py` 修复，mock 测试和文档构建均通过，收尾干净 | true |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | Gemini 3 Pro | 84 | 构造了 Sphinx 项目并看到正确 HTML 输出；采用条件式 `__qualname__`，可见测试通过但保留了特殊根对象行为 | false |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | Gemini 3.5 Flash | 87 | 调查和可见测试很完整，91 个测试通过；但同时修改 `restify`/`stringify`，通用逻辑扩散带来隐藏回归风险 | false |

这里的“过程分”是依据上述 100 分细则对轨迹质量的人工评分，不是外部评测分数；它与 `resolved` 有意分开。

## 5. 可执行改进建议

1. **优先复现 PEP 560 路径。**  
   不要只检查 `Inherited.__bases__`，应同时打印 `Inherited.__orig_bases__`，并比较其中元素的类型、`__module__`、`__name__`、`__qualname__` 和 `__display_name__`。

2. **优先采用局部修复。**  
   对本问题，修改 `_MockObject.__init__()` 使 mock 实例拥有正确的 `__qualname__`，比改变通用 `restify()` 或 `stringify()` 更符合现有设计，也更不容易影响 typing、泛型和普通类。

3. **增加明确的回归断言。**  
   应加入一个继承 mock 类的 autodoc 测试，直接断言输出包含 `Bases: :py:class:\`missing_module.Class\``，同时保留普通 mock、泛型 mock、装饰器 mock 和非 mock 类测试。

4. **将验证分成三层。**
   - 最小 Python 复现；
   - `test_ext_autodoc_mock.py` 等目标测试；
   - `test_util_typing.py`、autoclass、automodule 或完整相关测试集。
   
   任一层失败都应说明原因，不应仅凭最后一次命令返回 0 宣称全部通过。

5. **减少无信息试错。**  
   依赖缺失时先确认环境并一次性安装；避免反复生成脚本、逐字符拼接文件或执行没有新观测结果的命令。

6. **提交前检查最终状态。**  
   应确认临时调试代码、测试脚本和 patch 文件不污染源码变更，并核对 patch 只包含目标源码文件。

## 6. 不能仅凭这些轨迹确定的结论

- 不能证明某个模型在一般 SWE-bench 任务上优于其他模型；样本量小，模型版本、运行日期和 agent 版本同时变化。
- 不能确定三个 `resolved=false` 的确切原因，尤其是 Gemini Pro 和 Gemini 3.5 Flash 的可见测试已经通过；需要隐藏测试日志或最终应用后的仓库状态。
- 不能仅凭轨迹判断补丁是否因提交协议、路径、暂存状态或外部应用失败而未被评测系统正确采用。
- 不能据此断言所有 Python 版本、所有 Sphinx 构建器和所有第三方 mock 用法都兼容；轨迹主要验证了当前 Sphinx 版本下的 autodoc 场景。
- 不能把“构建成功”解释为“文档语义完全正确”；必须检查具体生成的 `Bases` 文本和交叉引用目标。
