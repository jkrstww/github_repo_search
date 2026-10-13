# Matplotlib 26291 多模型轨迹评测报告

## 1. 评测范围与方法

目录中实际存在 10 个 ATIF JSON 轨迹，均已逐条检查任务描述、推理文本、命令、命令返回、最终补丁和提交步骤。标签分布为 **7 条 `resolved=true`、3 条 `resolved=false`**。

标签中提到的 `20260901_mini-v2.4.2_gemini-3-5-flash.json` 不在目录内，未纳入分析。

本报告中的“过程分”仅依据轨迹证据，不把 `resolved` 直接计入分数，以避免循环论证。所有轨迹最后都有提交命令，但 ATIF 均在该工具调用处结束，没有保存提交命令的 observation，因此只能确认“发起了提交”，不能确认补丁传输和评测端接收状态。

## 2. 总体结论

问题的关键不只是 `self.figure is None`，而是 `_tight_bbox.adjust_bbox` 会调用：

```python
locator(ax, None)
```

因此同一条路径上有两个相关条件：

1. `get_window_extent(None)` 会尝试使用 `self.figure._get_renderer()`；若 locator 未关联 Figure，就产生报告中的 `_get_renderer` 异常。
2. 即使先设置了 `self.figure`，原始局部变量 `renderer` 仍然是 `None`；后续 `get_offset(..., renderer)` 又会在 `renderer.points_to_pixels(...)` 处失败。

成功轨迹最终都保证了传给 `get_window_extent` 和 `get_offset` 的 renderer 非空。主要有三种形式：

- 最小方案：`renderer is None` 时直接取 `ax.figure._get_renderer()`，如 Gemini 3 Flash。
- 同时设置 `self.figure` 和 renderer，如 Claude Opus、GLM、MiniMax。
- 通过 `Artist.set_figure` 避开 `OffsetBox.set_figure` 对空 child 的传播，再补 renderer，如 GPT-5.2。

两个能够由代码直接解释的失败是：

- GPT-5 mini 改成 `get_bbox(renderer)`，但仍把 `None` 传给需要 renderer 的 `get_bbox/get_offset`。
- Kimi K2.5 只设置 `self.figure`，解决了第一处异常，却没有解决后续 `get_offset` 的空 renderer。

第三个失败 Gemini 3 Pro 是重要反例：其最终补丁与三条成功轨迹的补丁逐字等价，轨迹中精确复现成功且 49 项模块测试通过。单凭轨迹，不能将其 `resolved=false` 归因于修复逻辑。

过程分上，成功组为 82–96 分，均值约 88.9；失败组为 36、43、91 分。前两个失败体现了明显过程缺陷，91 分的 Gemini 3 Pro 则表明外部结果还受到轨迹外因素影响。因此，“完整理解 renderer 数据流并取得有效 pass-after 证据”与成功高度相关，但不是 `resolved` 的充分条件。

## 3. 可复用的 100 分评分细则

| 维度 | 权重 | 高档 | 中档 | 低档 | 主要扣分条件与可观察证据 |
|---|---:|---|---|---|---|
| 任务理解与复现 | 15 | 13–15：识别真实触发条件，并取得 failure-before | 7–12：理解现象但复现不完全 | 0–6：仅复述或测试了错误路径 | 未覆盖 `bbox_inches='tight'`/等价调用扣 3–6；无失败栈扣 2–4。证据为复现脚本、栈和返回码 |
| 定位与根因推导 | 20 | 17–20：追踪完整调用链和所有空值传播 | 9–16：找到故障点但遗漏后续约束 | 0–8：猜测式修改 | 只看首个异常、不检查被调函数契约扣 5–10；与代码相矛盾的假设扣 4–8 |
| 工具与命令质量 | 10 | 9–10：查询聚焦、环境可控、命令结果可信 | 5–8：有冗余但可恢复 | 0–4：大量试错或破坏环境 | 无约束安装/反复切换依赖扣 1–5；管道掩盖退出码扣 2–4；危险或无关命令扣 2–6 |
| 修改正确性与范围 | 25 | 22–25：覆盖根因、最小、符合现有契约 | 11–21：能工作但冗余或扩大范围 | 0–10：关键路径仍失败 | 遗留必现错误时本维度最高 10；无关源文件修改扣 3–8；绕过生命周期 API 或引入状态风险扣 1–5 |
| 测试与验证证据 | 20 | 17–20：failure-before/pass-after、精确复现、相关回归测试 | 8–16：仅自定义测试或覆盖有限 | 0–7：未执行、执行失败或只检查源码文本 | 捕获异常后仍退出 0 不算通过；`| head/tail` 掩盖 pytest 状态扣 3–6；未确认导入的是工作树代码扣 3–5 |
| 错误恢复与收尾 | 10 | 9–10：根据反馈修正、检查最终 diff、干净提交 | 5–8：完成但证据或清理不足 | 0–4：忽略失败或提交内容不明 | 无视失败输出扣 3–6；未检查 patch 扣 2–4；提交范围混入测试/产物扣 3–6 |

总分档位：90–100 为“强证据、高可信”；75–89 为“基本可靠但有验证或效率缺口”；60–74 为“部分可信”；低于 60 为“关键技术或证据链不成立”。

该设计把“代码正确性”和“验证证据”合计设为 45%，因为轨迹评测的核心不是语言流畅度，而是补丁能否工作以及是否有可审计证据；同时保留根因、工具和收尾权重，用于区分偶然命中与稳定工程能力。

## 4. 逐轨迹诊断

表中分项顺序为：理解/定位/工具/代码/测试/收尾。

| 轨迹 | `resolved` | 过程分 | 诊断摘要 |
|---|---:|---:|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | true | **96**（15/19/7/25/20/10） | 精确复现；第一次只设 Figure 后观察到第二个 `points_to_pixels` 异常，再补 renderer；通过 5 个边界场景、gallery、6 个筛选测试及模块测试。依赖安装略冗长，但反馈闭环最完整。 |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | true | **83**（14/17/4/20/18/10） | 最终同时处理 Figure、renderer，并验证多种 inset 场景；但经历 95 步反复修改，且为支持 `self.set_figure` 扩大修改到通用 `OffsetBox.set_figure`，范围和回归风险高于必要值。 |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | true | **88**（14/18/6/24/16/10） | 从首个修复留下的 `get_offset(None)` 错误中恢复，形成正确的三行修复；原问题及四类自定义场景通过，但没有运行仓库 pytest。 |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | true | **93**（15/18/6/25/20/9） | 最终采用最小方案：在任何 bbox/offset 计算前取得 renderer；49 项 `axes_grid1` 测试及百分比、zoomed inset 场景通过。前期多次试改，但最终 diff 干净。 |
| `20260217_mini-v2.0.0_glm-5-high.json` | true | **82**（13/17/3/24/16/9） | 后期正确识别 `get_offset` 仍需 renderer，并验证直接 locator、`adjust_bbox`、subfigure 和多 locator；但 84 步中有大量环境折腾，若干测试用 `try/except` 吞掉失败，且没有仓库测试通过证据。 |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | true | **92**（15/20/8/24/15/10） | 根因分析最细：确认 `OffsetBox.set_figure` 会遍历 `[None]`，因此显式调用 `Artist.set_figure`；精确复现 pass-after。代码略重且未执行仓库测试，因此验证分低于最高档。 |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | false | **36**（9/9/4/6/0/8） | 将 `get_window_extent` 换成 `get_bbox`，但仍向 `get_bbox/get_offset` 传入 `None`；唯一运行验证因 `ModuleNotFoundError` 失败，且脚本未使用 tight bbox。最终没有任何运行时正确性证据。 |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | false | **43**（11/11/2/8/2/9） | 只增加 `self.figure = ax.figure`，遗漏 `renderer` 仍为空；所谓“fix-only”测试只搜索源码字符串。真正测试失败，pytest 也失败，且 `| head` 令 shell 返回码看似为 0。 |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | true | **88**（14/18/2/24/20/10） | 最终修复正确，并通过精确复现、5 个扩展场景及 6 个相关仓库测试；但共有大量依赖错误，并直接删除 site-packages 下 NumPy 文件，工具纪律明显不足。 |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | false | **91**（15/18/5/24/20/9） | 最终补丁与多条成功轨迹相同；精确 tight-bbox 复现成功，49 项测试通过。中途 `sed` 重构较冒险，但最终 diff 干净。过程证据不支持外部失败标签。 |

## 5. 关键案例

### 成功案例：Claude 4.5 Opus

在 [轨迹 step 17](/Users/zzk/project/github_repo_search/src/trajectory_analyse/experiments/diff_trajectories/verified/matplotlib__matplotlib-26291/20260217_mini-v2.0.0_claude-4-5-opus-high.json:713) 中，模型使用 `bbox_inches='tight'` 得到与报告一致的 `self.figure._get_renderer()` 异常。第一次只设置 Figure 后，[step 25](/Users/zzk/project/github_repo_search/src/trajectory_analyse/experiments/diff_trajectories/verified/matplotlib__matplotlib-26291/20260217_mini-v2.0.0_claude-4-5-opus-high.json:1094) 暴露了第二层错误：`renderer.points_to_pixels` 的 renderer 仍为 `None`。模型没有忽略这一反馈，而是补充 renderer fallback；[step 29](/Users/zzk/project/github_repo_search/src/trajectory_analyse/experiments/diff_trajectories/verified/matplotlib__matplotlib-26291/20260217_mini-v2.0.0_claude-4-5-opus-high.json:1284) 精确复现通过，[step 35](/Users/zzk/project/github_repo_search/src/trajectory_analyse/experiments/diff_trajectories/verified/matplotlib__matplotlib-26291/20260217_mini-v2.0.0_claude-4-5-opus-high.json:1569) 的 6 项相关 pytest 也全部通过。

这条轨迹说明，成功的决定性因素不是第一次猜中，而是把新的异常视为调用链证据并继续修正。

### 失败案例：Kimi K2.5

Kimi 的补丁只加入 `self.figure = ax.figure`。它实际上编写了直接调用 `locator(ax, None)` 的测试，但 [step 44](/Users/zzk/project/github_repo_search/src/trajectory_analyse/experiments/diff_trajectories/verified/matplotlib__matplotlib-26291/20260217_mini-v2.0.0_kimi-k2-5-high.json:1703) 在更早的普通绘图阶段因环境问题失败，未执行到关键断言。随后用源码中是否包含赋值语句作为“SUCCESS”，不能证明行为正确。[step 50](/Users/zzk/project/github_repo_search/src/trajectory_analyse/experiments/diff_trajectories/verified/matplotlib__matplotlib-26291/20260217_mini-v2.0.0_kimi-k2-5-high.json:1945) 的 pytest 明确显示 FAILED，却因 `2>&1 | head` 返回了管道末端的成功状态。

其失败与外部标签一致：修复只消除了首个异常，进入 `get_offset` 后仍会因 renderer 为 `None` 失败。

### 标签反例：Gemini 3 Pro

[step 14](/Users/zzk/project/github_repo_search/src/trajectory_analyse/experiments/diff_trajectories/verified/matplotlib__matplotlib-26291/20260226_mini-v2.0.0_gemini-3-pro-high.json:484) 成功复现原始异常；修改后 step 43 输出 `Success`，而 [step 47](/Users/zzk/project/github_repo_search/src/trajectory_analyse/experiments/diff_trajectories/verified/matplotlib__matplotlib-26291/20260226_mini-v2.0.0_gemini-3-pro-high.json:1870) 显示 49 项测试全部通过，[step 63](/Users/zzk/project/github_repo_search/src/trajectory_analyse/experiments/diff_trajectories/verified/matplotlib__matplotlib-26291/20260226_mini-v2.0.0_gemini-3-pro-high.json:2542) 又联合验证了复现和完整模块测试。最终 diff 的目标 blob `59bcafc4d9` 与 Claude Opus、GLM、MiniMax 的成功补丁相同。

因此，这一条只能诊断为“过程和可见产物高质量，但外部未 resolved”；不能据此断言 Gemini 3 Pro 的修复逻辑错误。

## 6. 可执行改进建议

1. 固定最小复现为 Agg 后端加 `fig.savefig(..., bbox_inches='tight')`，并要求修改前失败、修改后通过。
2. 沿完整调用链检查参数：这里必须同时审查 `get_window_extent`、`get_bbox` 和 `get_offset` 对 renderer 的要求。
3. 优先采用局部最小修复，即在 `AnchoredLocatorBase.__call__` 开头补足 renderer；只有明确存在生命周期需求时才设置 Figure。
4. 测试分三层执行：精确复现、直接 `locator(ax, None)`、相关模块 pytest；三层都应保留未被管道掩盖的返回码。
5. 禁止用“源码包含某字符串”代替行为测试；测试脚本捕获异常时必须重新抛出或显式 `sys.exit(1)`。
6. 使用 `set -o pipefail`，或不对 pytest 输出直接接 `head/tail`，防止失败被误报为成功。
7. 在安装依赖前读取项目约束并固定兼容版本；避免反复升级/降级 NumPy，更不应直接删除全局 site-packages。
8. 提交前记录 `git diff --check`、最终 diff、测试命令与返回码，并由执行框架保存提交命令的 observation。
9. 对“相同补丁、不同 resolved”样本重新运行隐藏评测并保存 grader 日志、补丁哈希和容器标识，以区分标签问题、提交问题和非确定性。

## 7. 仅凭这些轨迹不能确定的事项

- 不能确定 Gemini 3 Pro 失败的真实原因；隐藏测试、补丁接收、超时、容器差异和标签生成过程均不可见。
- 不能证明某个模型家族或 reasoning 档位具有统计显著优势：每个设置只有一次运行，日期、价格、依赖状态和探索长度也未受控。
- 不能把高步骤数直接解释为更强或更弱；长轨迹既可能代表有效恢复，也可能只是环境试错。
- 不能据本样本判断不同修复方案在 Matplotlib 全量测试、跨后端、跨 Python 版本或 locator 跨 Figure 复用时的长期兼容性。
- 不能把本地 pytest 通过等同于最终正确，尤其是测试环境曾被运行时安装和替换依赖的轨迹。
- 不能确认最后的提交命令实际执行成功，因为所有轨迹都缺少该最后一步的 observation。
