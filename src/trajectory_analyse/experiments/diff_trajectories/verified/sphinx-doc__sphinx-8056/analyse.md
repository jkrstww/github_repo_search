# Sphinx-8056 轨迹评测报告

## 1. 总体结论

本目录包含 11 条 ATIF 轨迹：

- `resolved=true`：5 条，成功率 45.5%
- `resolved=false`：6 条，失败率 54.5%

任务的核心问题是：NumPy 风格文档中的

```text
x1, x2 : array_like, optional
```

经过 Napoleon 转换后被生成为：

```text
:param x1, x2: ...
:type x1, x2: ...
```

下游 Sphinx 字段处理器不会把 `x1, x2` 识别为两个独立参数，因此应在 Napoleon 生成 docutils 字段之前拆分参数名。

成功轨迹的共同特征是：

1. 能准确复现原始错误，观察到 `:param x1, x2:` 和 `:type x1, x2:`。
2. 将修复定位到 `sphinx/ext/napoleon/docstring.py` 的 `_format_docutils_params`。
3. 为每个参数生成独立的 `:param` 和 `:type` 字段。
4. 验证 `optional`、无类型、多个参数、`*args`/`**kwargs`、`napoleon_use_param` 等场景。
5. 至少检查最终 diff，并形成可提交补丁。

失败轨迹主要有四类问题：

- 只修改 `_format_field`，只能影响 `napoleon_use_param=False` 的字段列表路径，无法修复默认的 `:param` 路径。
- 修改 `sphinx/util/docfields.py`，试图让下游解析器猜测逗号含义，结果产生错误类型绑定。
- 在 `_consume_fields` 层全局拆分字段，影响参数之外的其他 NumPy 字段，兼容性风险较大。
- 虽然修改了目标函数，但只支持 `", "`，没有覆盖无空格逗号、特殊参数名或完整回归验证。

模型版本与成功率不存在简单的单调关系：Claude Opus 4.5 成功而 Opus 4.6 失败；GPT-5-mini 成功而 GPT-5.2 失败；Gemini 3 Flash 失败而 Gemini 3.5 Flash 成功。这说明本样本中，轨迹中的定位、假设修正和验证行为比模型名称本身更能解释结果。

证据强度方面：

- 强证据：轨迹明确记录了修改前后的解析输出、HTML 构建结果、测试命令及 diff。
- 中等证据：只运行临时脚本或字符串输出，没有完整测试套件。
- 弱证据：依赖安装失败、命令超时、输出被截断，或最终只提交补丁但没有证明补丁适用于真实 Sphinx 构建。

`resolved` 是外部评测标签，只能说明最终基准是否判定成功，不能单独证明过程正确，也不能证明某一次测试失败就是代码回归。

## 2. 可复用的 100 分轨迹评测细则

| 维度 | 权重 | 高分档位 | 中分档位 | 低分档位及扣分条件 | 可观察证据 |
|---|---:|---|---|---|---|
| 任务理解与验收标准 | 15 | 13–15：明确 NumPy 参数合并、`optional`、两种输出模式及预期 | 9–12：理解核心问题，但遗漏配置分支或渲染层 | 4–8：只描述“格式异常”；0–3：误解需求 | 复现输入、预期输出、配置项说明 |
| 定位与因果假设 | 15 | 13–15：定位 `_format_docutils_params`，并理解下游字段解析 | 9–12：找到 Napoleon 相关代码，但未验证调用链 | 4–8：只改表现层；0–3：修改无关模块 | 搜索路径、调用关系、HTML/AST 观察 |
| 代码修改正确性与范围 | 25 | 22–25：每个参数独立生成字段，保留类型、`optional`、描述，兼容边界情况，改动最小 | 16–21：核心案例有效，但边界或兼容性不足 | 8–15：只修复一个配置分支、全局副作用或描述丢失；0–7：错误层级或代码明显失效 | diff、输出结果、特殊参数测试 |
| 验证充分性 | 25 | 22–25：精确复现、HTML 构建、目标测试、回归测试均有证据 | 16–21：有多组脚本和部分测试 | 8–15：只打印字符串或只测单一路径；0–7：未验证、忽略失败 | 命令返回码、断言、HTML 片段、测试摘要 |
| 错误恢复与工具纪律 | 10 | 9–10：依赖缺失后有序恢复，区分环境问题和代码问题 | 6–8：能恢复，但存在重复尝试或低效命令 | 3–5：长时间重复失败；0–2：使用破坏性命令或丢失上下文 | 安装恢复、失败分类、是否使用 `git checkout` 等 |
| 收尾与证据闭环 | 10 | 9–10：清理临时文件，确认只改源文件，检查最终 diff/补丁 | 6–8：有 diff 和提交步骤，但清理或验证不完整 | 3–5：声称“通过”但缺少输出；0–2：无最终状态证据 | `git diff`、`git status`、补丁内容、最终命令 |

评分档位：

- 85–100：优秀，定位、实现、验证和收尾均形成闭环。
- 70–84：良好，核心修复可信，但测试覆盖或工程纪律略有不足。
- 50–69：部分有效，能处理主案例，但存在明显边界、范围或证据问题。
- 0–49：较差，修复层级错误、核心路径未覆盖，或几乎没有可靠验证。

这样设计的原因是：实现正确性与验证合计 50 分，直接对应“是否真正修复以及是否证明修复”；任务理解和定位合计 30 分，用于区分偶然改对与有因果依据的修复；错误恢复和收尾合计 20 分，用于评价工程可复现性和提交质量。

## 3. 具体案例

### 成功案例：`20260217_mini-v2.0.0_kimi-k2-5-high.json`

过程质量较高，`resolved=true`。

关键事件：

- 轨迹先查看了 `NumpyDocstring`、`_consume_field` 和 `_format_docutils_params`，确认输入被解析为单个 `_name = "x1, x2"`。
- 初始输出明确显示：

  ```text
  :param x1, x2: ...
  :type x1, x2: ...
  ```

- 修改后输出变为：

  ```text
  :param x1: ...
  :type x1: :class:`array_like`, *optional*
  :param x2: ...
  :type x2: :class:`array_like`, *optional*
  ```

- 额外检查了 `Keyword Arguments`、没有描述的参数、多个参数、`optional` 和 `napoleon_use_keyword`。
- 运行了 Napoleon 相关测试；测试环境中有若干与 docutils 版本和额外警告有关的失败，但轨迹把这些失败与本次修改区分开，并继续运行可通过的测试。
- 最终 diff 只涉及 `sphinx/ext/napoleon/docstring.py`。

这些证据支持以下判断：该轨迹不仅在字符串层面看到了修复，也验证了参数类型和 `optional` 信息在拆分后仍被保留。其主要不足是没有提供完整、干净环境下的全量测试结果。

评估分数：85/100。

### 失败案例：`20260217_mini-v2.0.0_gpt-5-2-high.json`

过程分析能力较好，但实现方向错误，`resolved=false`。

关键事件：

- 轨迹正确追踪到了 Sphinx 的 `TypedField` 和 `DocFieldTransformer`，并构建了真实 Sphinx HTML 示例。
- 观察到合并字段的错误表现，说明其理解了问题发生在 Napoleon 输出与下游字段转换之间。
- 但最终修改的是 `sphinx/util/docfields.py`，添加了“如果第一个 token 以逗号结尾，就不要当作类型”的启发式。
- 实际构建结果仍然错误，HTML 中出现类似：

  ```html
  <strong>x2</strong> (<em>x1</em><em>,</em>)
  ```

  即 `x1,` 被错误解析成类型，而不是两个参数名。
- 轨迹只运行了 `py_compile` 和有限的测试，没有证明完整 Napoleon 行为已经恢复。

这说明该轨迹的诊断阶段比实现阶段更强：它找到了症状出现的下游位置，却没有遵循“在信息最完整的上游拆分”的原则。下游解析器本来无法可靠区分逗号分隔的参数名和类型语法，继续增加启发式反而扩大了风险。

评估分数：62/100。

## 4. 每条轨迹诊断摘要

| 轨迹文件 | 过程质量 | 关键诊断 | `resolved` |
|---|---:|---|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | 78/100 | 准确复现并修改 `_format_docutils_params`；验证了 optional 和多种边界，但 pytest 安装/运行不完整 | true |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | 57/100 | 在 `NumpyDocstring._consume_fields` 中全局拆分字段；临时测试较多，但修改范围过宽，可能影响 returns、attributes 等非参数字段 | false |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | 67/100 | 修改了正确函数并做了较多验证；但只按 `", "` 拆分，且后续参数的描述处理不对称，边界覆盖不足 | false |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | 48/100 | 先改 `_format_field`，后来才意识到 `napoleon_use_param=True` 路径未修复；依赖和测试过程混乱，并使用了 `git checkout` | false |
| `20260217_mini-v2.0.0_glm-5-high.json` | 45/100 | 主要修复 `_format_field`，证明了字段列表模式可改善，但核心 `:param` 模式仍曾输出 `x1, x2`；验证重点偏离默认路径 | false |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | 62/100 | 深入检查了真实 HTML 和 `TypedField`，诊断有价值；最终改错层级，启发式导致类型绑定错误 | false |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | 76/100 | 目标函数定位和补丁方向正确，完成了 exact repro、optional 和 edge case；没有可用的完整 pytest 结果，且有较粗糙的暂存操作 | true |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | 85/100 | 定位准确，覆盖 `param`、`keyword`、optional 和边界，能区分环境测试失败；最终 diff 较集中 | true |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | 88/100 | 反复比较原始行为与修改行为，处理了描述重复、NumPy/Google 风格和回归测试，收尾证据完整 | true |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | 64/100 | 做了真实 Sphinx 构建并最终修改了目标函数；但构建输出曾显示错误字段绑定，后续缺少足够的重新构建和测试闭环 | false |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | 84/100 | 先分析 `TypedField`，再修复 Napoleon formatter；构建 HTML 验证了两个参数和 optional，测试中仅剩环境相关失败 | true |

这里的“过程质量”是基于轨迹证据计算的独立评价，不等同于 `resolved`。例如 Gemini 3 Pro 和 GPT-5.2 都有较好的定位探索，但最终代码仍未通过外部评测；反过来 GPT-5-mini 的测试环境不完整，却因为修复路径和核心输出正确而获得成功标签。

## 5. 可执行改进建议

### 对代理执行流程

1. **先建立最小失败基线**

   同时记录：

   - `NumpyDocstring` 的字符串输出；
   - `napoleon_use_param=True/False`；
   - 真实 Sphinx HTML 或 docutils field AST。

2. **沿数据流定位，而不是只看最终 HTML**

   对本问题，参数名在 `_consume_field` 中仍然保留为完整字符串，因此最佳拆分点是 `_format_docutils_params`。不要优先修改下游 `TypedField` 或 `DocFieldTransformer`。

3. **明确两条配置路径**

   至少测试：

   - `napoleon_use_param=True`：独立 `:param`/`:type`；
   - `napoleon_use_param=False`：字段列表中的独立参数显示。

4. **使用专门的参数名拆分逻辑**

   建议抽取小型、可测试的 helper，覆盖：

   - `x1, x2`
   - `x1,x2`
   - `x1, x2, x3`
   - `*args, **kwargs`
   - 空白和尾逗号
   - 不应拆分的类型表达式不被误伤

5. **验证描述和类型的对应关系**

   每个拆分后的参数都应保留共享描述和类型；`optional` 必须分别出现在每个参数的类型信息中。

6. **把测试环境问题单独分类**

   `ModuleNotFoundError`、依赖安装超时、docutils 版本差异和代码失败不能混为一谈。应记录：

   - 命令是否执行；
   - 返回码；
   - 失败是否发生在测试收集阶段；
   - 是否与修改前相同。

7. **提交前做最终闭环**

   清理临时脚本，检查 `git status`，确认 diff 只包含必要源文件，并重新运行最小复现和至少一个真实 Sphinx 构建。

### 对评测与数据收集流程

- 固定 Python、Sphinx、docutils、pytest 版本，减少“环境警告导致测试失败”。
- 保存完整命令输出，避免只保留截断后的日志。
- 将“补丁是否应用成功”“目标测试是否通过”“外部 resolved”分成独立标签。
- 对最终提交的补丁做静态检查，确认没有只修复 `napoleon_use_param=False` 的假阳性。
- 增加隐藏测试，覆盖无空格逗号、`Keyword Arguments`、`Other Parameters`、`*args`/`**kwargs` 和多行描述。

## 6. 不能仅凭这些轨迹确定的结论

1. 不能据此断言某个模型本身一定比另一个模型更适合该类任务；模型版本、提示配置、运行时间和依赖环境同时变化。
2. 不能仅凭 `resolved=false` 判断代理完全没有有效工作；若干失败轨迹已经正确定位问题或完成部分路径修复。
3. 不能仅凭 `resolved=true` 证明补丁覆盖所有隐藏边界；成功标签只说明外部评测接受了该结果。
4. 不能确定重复共享描述是否符合所有用户对最终文档呈现的偏好；轨迹主要验证了结构和类型信息。
5. 不能完全排除外部评测、缓存构建、版本差异或补丁提交过程对 `resolved` 的影响。
6. 由于轨迹中的工作目录是 `/testbed`，且本报告未执行这些代理的补丁，不能把日志中的最终文件状态等同于当前目录的实际状态。
