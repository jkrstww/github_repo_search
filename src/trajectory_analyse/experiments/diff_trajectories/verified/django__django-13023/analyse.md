# Django-13023 轨迹评测报告

## 1. 总体结论

本样本共有 11 条轨迹，其中 `resolved=true` 8 条，`resolved=false` 3 条。任务的技术核心是：

```python
try:
    return decimal.Decimal(value)
except decimal.InvalidOperation:
```

应扩展为捕获 `TypeError`，并且从更完整的边界测试看，还应捕获 `ValueError`：

```python
except (decimal.InvalidOperation, TypeError, ValueError):
```

### 成功轨迹的共性

8 条成功轨迹最终都具备以下特征：

1. 正确定位到 `django/db/models/fields/__init__.py` 中模型 `DecimalField.to_python()`。
2. 能够复现字典输入导致的 `TypeError`。
3. 将异常处理扩展到至少 `TypeError` 和 `ValueError`。
4. 最终补丁只修改目标源文件，没有提交测试脚本或配置文件。
5. 大多数轨迹执行了针对性测试，部分还执行了 `model_fields` 或 `model_fields.test_decimalfield` 测试。
6. 最后生成并输出了 `patch.txt`。

### 失败轨迹的共性

两条 GPT 轨迹的共同问题是只捕获 `TypeError`，没有处理 `decimal.Decimal()` 对列表等输入产生的 `ValueError`：

- `gpt-5-2-high`：最终补丁为 `except (decimal.InvalidOperation, TypeError)`。
- `gpt-5-mini`：最终也只捕获 `TypeError`，且其自身测试已经观察到列表输入仍然抛出 `ValueError`，但没有继续修复。

这说明失败并不只是“没有找到代码”，而是边界条件覆盖不足、已发现的失败没有闭环处理。

`gemini-3-pro-high` 是特殊案例：轨迹中先发现 `ValueError` 漏捕，随后修复为完整的三类异常，目标测试也通过，但外部标签仍为 `resolved=false`。这证明：

- `resolved` 是外部评测结果，不等价于轨迹中某一步的局部正确性；
- 可能存在补丁提取、提交时机、隐藏测试、环境或评测基础设施差异；
- 仅凭 ATIF 轨迹无法确定该条为何被外部判定失败。

### 证据强弱与局限

证据强度大致如下：

1. **强证据**：最终 `git diff`、目标测试通过、复现脚本明确输出 `ValidationError`。
2. **中等证据**：自定义脚本覆盖多个输入类型，但没有完整测试套件。
3. **弱证据**：模型在消息中声称“修复完成”，但没有展示命令输出。
4. **外部标签**：`resolved` 对最终评测最重要，但其内部判定过程不可见，不能单独用来评价每一步过程。

## 2. 可复用的 100 分轨迹评分细则

| 维度 | 权重 | 评分档位 | 可观察证据与扣分条件 |
|---|---:|---|---|
| 任务理解与验收标准 | 15 | 13-15：明确要求字典输入转为 `ValidationError`，并识别异常语义；8-12：理解主问题但边界不完整；1-7：只机械修改；0：误解任务 | 未区分模型字段与表单字段、未明确异常类型，扣 3-8 分 |
| 定位与根因分析 | 15 | 13-15：定位正确方法并解释 `Decimal()` 异常来源；8-12：定位正确但分析浅；1-7：搜索范围混乱；0：改错文件 | 只看 forms 字段、未确认原始异常，扣 4-10 分 |
| 代码修改正确性与范围 | 25 | 23-25：捕获 `InvalidOperation/TypeError/ValueError`，只改目标源文件；16-22：功能基本正确但覆盖不全；1-15：补丁不完整或有越界修改；0：无有效修改 | 漏掉 `ValueError`，建议扣 8-12 分；修改测试/配置/无关文件，扣 5-15 分 |
| 验证覆盖与测试质量 | 25 | 23-25：覆盖 dict、list、object、非法字符串、合法值，并运行目标测试；16-22：有复现和部分回归测试；8-15：只有单一手工测试；0-7：无有效验证 | 已看到失败仍未修复，扣 8-15 分；只运行无关测试，扣 5-10 分 |
| 错误恢复与工具选择 | 10 | 9-10：能处理依赖缺失、命令失败、编码问题并继续；6-8：恢复有效但较绕；1-5：反复失败或忽略错误；0：被环境问题阻断 | 未处理 `ModuleNotFoundError`、测试失败或错误命令，扣 2-6 分 |
| 最终收尾与证据链 | 10 | 9-10：检查 diff、状态、patch 内容并按要求提交；6-8：生成补丁但证据不完整；1-5：只有口头总结；0：无提交证据 | 未确认只改源文件、未输出最终 patch，扣 3-8 分 |

设计理由：代码修改和验证共占 50 分，因为该任务的成功关键是补丁语义和边界行为；任务理解、定位各占 15 分，用于区分真正的根因分析和偶然命中；错误恢复及收尾各占 10 分，反映工程执行能力和结果可审计性。

## 3. 具体案例

### 成功案例：`20260217_mini-v2.0.0_glm-5-high.json`

关键证据：

- 轨迹先验证字典、列表、集合、对象、字符串、整数、浮点数和 `None`。
- 自定义测试输出：
  - `PASS: dict correctly raised ValidationError`
  - `PASS: list correctly raised ValidationError`
  - `PASS: set correctly raised ValidationError`
  - `PASS: object correctly raised ValidationError`
- 最终 diff 为：

```diff
-except decimal.InvalidOperation:
+except (decimal.InvalidOperation, TypeError, ValueError):
```

- `model_fields` 测试通过。
- `validators` 测试出现并行运行器的 `cannot pickle 'traceback' object`，但该错误与本次补丁无直接关系，且目标模型字段测试已经通过。

该轨迹的证据链完整：理解问题、覆盖异常类型、验证正常值、执行回归测试、检查补丁并提交。因此 `resolved=true` 与过程证据一致。

### 失败案例：`20260217_mini-v2.0.0_gpt-5-mini.json`

关键事件：

1. 初次使用 `git apply` 失败，随后改用 `perl` 修改。
2. 修复后测试结果显示：
   - 字典输入已转为 `ValidationError`；
   - 对象输入已转为 `ValidationError`；
   - 列表输入仍然是 `ValueError (FAIL)`。
3. 轨迹随后继续生成补丁，但没有把异常处理改为包含 `ValueError`。
4. 最终 diff 仍为：

```diff
-except decimal.InvalidOperation:
+except (decimal.InvalidOperation, TypeError):
```

这条轨迹已经获得了足以证明补丁不完整的反例，却没有继续修复。其 `resolved=false` 与过程证据高度一致。问题不是定位错误，而是测试反馈没有驱动后续修改。

### 标签与过程不一致案例：`20260226_mini-v2.0.0_gemini-3-pro-high.json`

该轨迹先得到：

```text
Caught ValueError (FAIL): argument must be a sequence of length 3
```

随后将捕获范围扩展为：

```python
except (decimal.InvalidOperation, TypeError, ValueError):
```

并再次运行复现脚本和 `model_fields.test_decimalfield`，均显示通过，最终 patch 也只包含目标源文件的单行修改。

因此，从可见过程质量看，它应属于高质量轨迹；但外部标签是 `resolved=false`。这说明评测结果可能受隐藏测试、提交提取、版本环境或其他轨迹外部因素影响，不能把该标签简单解释为“代码一定错误”。

## 4. 各条轨迹诊断摘要

| 轨迹文件 | 过程质量诊断 | 最终 `resolved` |
|---|---|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | 定位准确；测试 dict、list、object、非法字符串；补丁捕获三类异常；依赖和测试环境问题均恢复；目标测试通过。 | `true` |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | 探索较充分，比较模型字段与表单字段；经历编码和依赖问题后恢复；自定义验证及 `model_fields` 测试通过；补丁正确。 | `true` |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | 步骤少但根因明确，补丁捕获三类异常，字典复现成功；缺少完整回归测试，验证深度一般。 | `true` |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | 轨迹很长，包含较多探索；曾使用错误测试标签并产生测试错误，也测试了超出任务范围的 forms 行为；最终补丁正确并完成提交。 | `true` |
| `20260217_mini-v2.0.0_glm-5-high.json` | 边界测试最完整之一，目标测试通过；另一个 validators 测试受并行序列化错误影响，但未显示为补丁回归。 | `true` |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | 正确定位并验证 dict；依赖问题恢复；但只捕获 `TypeError`，未测试或处理 `ValueError`；没有完整回归测试。 | `false` |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | 初次补丁命令失败后恢复；明确观察到 list 的 `ValueError` 失败，却停止在只捕获 `TypeError` 的补丁；测试闭环不完整。 | `false` |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | 先检查模型字段和表单字段；自定义表单测试及模型字段测试证据较充分；环境问题导致部分早期测试失败，但最终补丁正确。 | `true` |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | 补丁正确；运行 `model_fields`、`validation`，并测试模型 `full_clean()` 对字典值产生 `ValidationError`；中间有把表单转换行为与模型字段混淆的探索。 | `true` |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | 先发现只捕获 TypeError 不够，随后根据 ValueError 反例修复；目标测试通过；过程质量高，但与外部标签不一致。 | `false` |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | 系统检查实现、异常继承关系、表单字段和测试文件；处理 Unicode 输出问题；`model_fields` 和目标测试通过；补丁干净。 | `true` |

按上述评分细则，过程质量可粗略分为：

- 90 分以上：Claude Opus 4.5、Claude Sonnet 4.5、GLM-5、Gemini 3.5 Flash。
- 85-89 分：Claude Opus 4.6、Kimi K2.5、MiniMax 2.5、Gemini 3 Pro。
- 60-75 分：GPT-5.2、GPT-5-mini，主要扣分来自漏捕获 `ValueError` 和验证闭环中断。

这些是基于轨迹证据的审查分，不是外部评测分。

## 5. 可执行的改进建议

1. **先枚举底层 API 的异常类型**  
   对 `decimal.Decimal()` 至少测试 dict、list、object、非法字符串，避免只围绕 PR 描述中的 dict 做最小修复。

2. **把“已发现失败”作为强制停止条件**  
   一旦测试输出 `FAIL`，必须继续修改或明确证明该行为不属于任务范围。GPT-mini 的主要问题正是看到了 `ValueError` 失败却仍提交。

3. **区分模型字段与表单字段**  
   本任务要求修改模型 `DecimalField`。表单 `DecimalField` 的行为可作为参考，但不应把表单测试结果当作模型字段修复证据。

4. **验证正常路径和异常路径**  
   至少同时验证 `None`、整数、浮点数、合法字符串、`Decimal` 实例，以及 dict、list、object、非法字符串。

5. **运行最相关的回归测试**  
   推荐顺序为：
   - 单独复现 `DecimalField.to_python()`；
   - `model_fields.test_decimalfield`；
   - 必要时运行 `model_fields`；
   - 对无关测试失败进行归因，不要把环境或并行测试框架错误误判为补丁错误。

6. **收尾时保存可审计证据**  
   最终应检查 `git diff`、`git status`、patch 文件内容，确认只包含目标源文件改动。

## 6. 不能仅凭轨迹确定的结论

- 不能确定 `gemini-3-pro-high` 为何在最终补丁看似正确的情况下得到 `resolved=false`，因为外部评测器的隐藏测试、补丁提取和提交状态不可见。
- 不能仅凭轨迹证明所有成功样本都通过了完全相同的测试集合；不同模型安装依赖、测试命令和输出截断情况不同。
- 不能据此断言某个模型在一般 Django 编程任务上必然更强；这里只观察到一个非常具体的异常处理任务。
- 不能仅依据步骤数量判断能力。Claude Opus 4.6 步骤较少但结果正确，而 Gemini Flash 步骤很多且包含冗余探索。
- 不能把测试环境中的依赖安装、编码错误或并行测试器异常直接归因于模型代码质量，除非它们最终影响了补丁内容或关键验证结果。
