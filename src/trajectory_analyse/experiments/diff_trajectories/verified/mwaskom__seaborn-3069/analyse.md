# 轨迹评测报告：`mwaskom__seaborn-3069`

## 总体结论

本样本共 11 条轨迹：外部 `resolved=true` 为 6 条，`false` 为 5 条。任务实质是让 objects API 的 `Nominal` 坐标轴与传统 categorical 行为对齐：默认边界为首尾类别外各 `0.5`、关闭该类别轴网格、当 nominal 位于 y 轴时反转 y 轴。

成功轨迹通常具备三个共同点：定位到 `Plotter._finalize_figure` 及 `Nominal` 生命周期；将改动集中在坐标轴收尾逻辑；用运行时断言检查 limits、grid 和 y inversion。失败轨迹更常出现编辑过程不稳定、为环境问题扩散修改到非目标模块、回归失败未收敛，或补丁无法重新应用。

但这不是“模型设置决定成功”的充分证据：`Claude Opus 4.6` 的过程测试很强却 `resolved=false`；`GPT-5 mini` 只做编译检查却 `resolved=true`；`MiniMax` 的补丁应用检查失败仍为 true。因此，`resolved` 应视为外部最终验收，不能反推每一步都正确，也不能据此断言模型能力高低或成功率差异。

证据强度分层如下：轨迹中实际命令输出的行为断言最强；pytest 输出次之，但须注意环境和截断；代理的文字宣称、静态字符串检查、仅生成 patch 最弱。所有样本都来自同一 PR，且环境中存在 pandas/Matplotlib 兼容噪声，结论仅适用于该任务与这些执行记录。

## 可复用的 100 分评分细则

| 维度 | 权重 | 13/18 分以上档 | 中档 | 低档与主要扣分 |
|---|---:|---|---|---|
| 任务理解与验收标准 | 15 | 明确三项行为，覆盖 inferred/explicit nominal、x/y、用户 limits | 只覆盖主要行为 | 误解需求、把 `resolved` 当过程证据，0–7 |
| 定位与假设验证 | 15 | 关联 `_finalize_figure`、`Nominal._setup`、旧 categorical 实现并验证假设 | 定位正确但未验证生命周期 | 盲改、仅猜测，0–7 |
| 修改设计与范围控制 | 20 | 最小改动，保留连续轴与用户设置，逻辑可维护 | 功能可行但有冗余状态/重复逻辑 | 混入无关兼容修改、脆弱文本替换、破坏既有路径，0–9 |
| 工具与命令质量 | 10 | 逐步读取、定向搜索、命令可复现并检查退出结果 | 有效但低效或局部不严谨 | 盲目 `sed`、错误 patch 命令、未核验结果，0–4 |
| 定向行为验证 | 20 | 断言 x/y、双 nominal、显式/推断、limits、grid、连续轴、facet 等 | 覆盖主路径但缺边界 | 仅静态检查或编译，0–9 |
| 回归测试与错误恢复 | 10 | 运行相关 pytest，区分环境/预存/引入问题并恢复临时实验 | 局部 pytest 且解释不完整 | 忽略失败、以错误调用替代测试，0–4 |
| 收尾、补丁与证据 | 10 | 最终 diff 最小、临时文件可解释、补丁可应用、提交证据完整 | 有 patch 但缺应用验证 | patch 不可应用、最终树状态不明，0–4 |

该设计让“定向行为验证”和“修改范围”占 40 分，因为此类任务的风险不在于写出若干行代码，而在于理解 scale 生命周期、保持连续坐标轴不变，并让用户 limits 与多子图行为一致。命令数量和 token 消耗不计分。

## 具体案例

### 成功案例：Claude Opus 4.5

`20260217_mini-v2.0.0_claude-4-5-opus-high.json` 先检查 `scales.py`、`plot.py` 和传统 categorical 实现；初始实验观测到 nominal x 的 limits 约为 `(-0.15, 3.15)`、网格开启，且 nominal y 未反转。随后修改 `_finalize_figure`，其综合测试实际输出了 x `(-0.5, 3.5)`、y `(3.5, -0.5)`、两轴 grid 关闭。

该轨迹还覆盖字符串类别、双 nominal、完整/部分用户 limits、连续轴未受影响，且遇到 pytest 未安装后补装并运行相关测试。其过程证据强，`resolved=true` 与过程判断一致；保留风险是 pytest 完整输出有截断，不能据此宣称全套测试无误。

### 失败案例：GPT-5.2

`20260217_mini-v2.0.0_gpt-5-2-high.json` 对核心三项行为的自定义验证看起来通过，但随后为 `mode.use_inf_as_null` 环境问题修改了 `_core/plot.py`、`_oldcore.py`、`categorical.py`。完整 `test_plot.py` 出现 `test_one_grouping_variable`、`test_facets_one_subgroup`、`test_move_with_range` 失败，且还曾以错误方式直接调用参数化测试，得到缺少 `split_var` 参数的 `TypeError`。

因此，核心实现并非没有证据，但改动范围从一个 objects API 行为扩散到传统 API 兼容层，回归失败与最终补丁交付均未充分收敛。`resolved=false` 与这一低质量收尾相符，但不能仅凭轨迹断言每个失败都由 pandas 兼容改动直接造成。

## 逐条诊断

| 轨迹文件 | 过程质量诊断 | `resolved` |
|---|---|---|
| `claude-4-5-opus-high` | 强（约 91）：定位完整，目标与边界行为验证最全面；pytest 证据部分截断。 | true |
| `claude-4-5-sonnet-high` | 中上（约 75）：修改集中于 `plot.py`，有集成检查；较多是代码结构检查，回归证据偏弱。 | true |
| `claude-4-6-opus` | 中上（约 82）：定向、facet 与相关 pytest 证据较强，区分了 `test_tick_minor` 的预存失败；最终 false 表明仍可能有隐藏语义或交付偏差。 | false |
| `gemini-3-flash-high` | 较弱（约 48）：初始诊断正确，但多次 `sed` 修改和循环结构修复显示状态不稳定；后续局部行为通过不足以证明最终 patch 可靠。 | false |
| `glm-5-high` | 强（约 87）：验证 inferred/explicit、limits、grid、y inversion、连续轴；对环境失败做了对照说明，改动仅在目标区域。 | true |
| `gpt-5-2-high` | 偏弱（约 58）：核心测试存在，但非目标 pandas 兼容修改扩大范围，相关 pytest 失败未闭环。 | false |
| `gpt-5-mini` | 中低（约 59）：定位和最小修改合理，但仅 `py_compile`，没有运行实际绘图或 pytest；外部成功不能补足过程证据。 | true |
| `kimi-k2-5-high` | 中上（约 76）：保存类别状态并做多场景自定义断言；pytest 安装/执行超时，补丁可应用性未充分证明。 | true |
| `minimax-2-5-high` | 中等（约 68）：目标行为和多数测试通过，但为环境问题改动范围扩大，且 `git apply --check` 明确失败。 | true |
| `gemini-3-pro-high` | 中等（约 63）：核心行为实验丰富，但部分 partial limit 预期不符，并且 `plot.py`、`scales.py` 的 patch 明确不能应用。 | false |
| `gemini-3-5-flash` | 中等（约 70）：`test_plot.py` 与 `test_scales.py` 曾通过且有综合验证；但为环境兼容修改四个文件，范围与最终 false 存在明显风险信号。 | false |

## 可执行改进建议

- 先写可执行验收矩阵：推断/显式 nominal、x/y、双 nominal、单类别、facet、完整与部分 limits、连续轴不变。
- 先复用旧 categorical 行为的语义，再决定状态来源；优先在 `_finalize_figure` 使用已建立的轴/scale 信息，避免为获取类别数引入无必要的持久状态。
- 环境兼容问题应先证明是基线问题；除非任务要求，否则不要把 `_oldcore.py`、`categorical.py` 的兼容修复混入功能补丁。
- 每次编辑后运行最小行为测试，再运行目标 pytest；失败应以基线对照、退出码和完整 traceback 分类，不能用代理文字替代证据。
- 提交前执行 `git diff --check`、检查工作树、`git apply --check` 于干净基线，并确保临时测试/patch 文件不污染交付物。
- 对反转 y 轴和用户 limits 增加专门断言，避免“默认 y 正确但显式/部分 limits 语义错误”。

不能仅凭这些轨迹确定隐藏评测的精确断言、每项 pandas/Matplotlib 失败是否完全预存、各模型的普遍工程能力，或 token/步数与成功之间存在因果关系。
