# Pytest-7490 轨迹评测报告

## 总体结论

共审阅 11 条 ATIF 轨迹，外部 `resolved=true` 为 4 条，`false` 为 7 条。成功轨迹的共同点不是“测试次数更多”，而是准确识别了 xfail 生命周期中的缓存语义：

- 正常执行时，`pytest_runtest_setup` 会写入 `xfailed_key`，其值可以是 `None`。
- 动态 marker 在测试体中加入后，`pytest_runtest_makereport` 必须刷新“此前缓存为 `None`”的结果。
- 但不能在 setup/teardown 报告中无条件重新求值，否则会对本来就在求值过程中报错的非法 xfail 条件二次求值。
- 合理边界是“仅 call report 且缓存为空”，或“缓存为空且 key 已存在”。前者避免非 call 阶段，后者区分“缓存的 None”和“根本未缓存”。

成功组的平均过程分为 **88.5/100**，失败组为 **62.4/100**。这支持“状态边界建模、失败后纠偏、有效验证”与最终解决显著相关，但不能据此断言模型能力的因果差异：样本仅 11 条，执行环境存在 Python 3.11 assertion-rewrite 兼容性故障，且 `resolved=false` 未提供隐藏评测的具体失败日志。

证据强度分层：

- **强证据**：轨迹内命令、返回码、实际 diff、测试摘要。
- **中等证据**：从代码控制流推断某 patch 会触发的回归；Gemini 3.5 Flash 轨迹实际复现了该类回归。
- **弱证据**：仅由 `resolved` 反推某个具体隐藏测试失败原因。报告未把它当作确定事实。

## 可复用的 100 分评测细则

| 维度 | 权重 | 16/优秀或满分档 | 中档 | 低档与主要扣分 | 可观察证据 |
|---|---:|---|---|---|---|
| 任务与约束理解 | 18 | 准确说明动态 marker、缓存、报告阶段和 `--runxfail` 的关系 | 知道需重算 marker，但缺少阶段/缓存边界 | 把“重算”当作全部答案；忽略行为兼容性，扣 6–18 | 推理文本、对 setup/call/makereport 的阅读 |
| 定位与假设 | 14 | 阅读 `skipping.py`、`nodes.py`、`Store` 和相关 git 历史，提出可证伪假设 | 只定位到 `skipping.py` | 未复现即编辑；假设与控制流脱节，扣 7–14 | 搜索命令、历史 diff、源码片段 |
| 复现与工具选择 | 14 | 先复现基线失败；遇环境问题后使用受控替代，如 `--assert=plain` | 有复现但命令噪声较大 | `|| true` 吞掉失败、管道掩盖返回码、全局安装/改无关源码，扣 4–14 | 命令退出码、临时测试、环境诊断 |
| Patch 的语义与范围 | 22 | 仅改必要源文件，保持缓存、阶段、`runxfail` 和静态 marker 语义 | 解决主复现但边界不完整 | 无条件重算、改变 `pytest_runtest_call` 既有缓存语义、改无关模块，扣 8–22 | 最终 `git diff`、调用顺序与条件判断 |
| 验证质量 | 22 | 主复现、静态 xfail、XPASS/strict、`--runxfail`、异常条件和相关测试文件均有可信结果 | 主复现加部分边界测试 | 只测 happy path；测试本身失败却宣称通过；未区分预期失败与基础设施失败，扣 6–22 | 测试输出、返回码、基线对照 |
| 恢复、清理与交付 | 10 | 明确诊断环境问题、比较基线、清理临时文件、核验 patch 和 git 状态 | 有清理/patch 核验但不完整 | 直接忽略错误、遗留无关改动、提交前未核验，扣 3–10 | `git status`、清理命令、patch 内容 |

这样设计的原因是该类任务的主要风险不在“找到一个能让单例变绿的改动”，而在于 hook 生命周期、缓存缺失与缓存 `None` 的区别。评分因此将 Patch 语义与验证合计设为 44 分，并将外部 `resolved` 保持为独立结果，不混入过程分。

## 具体案例

### 成功案例：`20260901_mini-v2.4.2_gemini-3-5-flash.json`

- Step 15 先得到原始复现的 `1 failed`。
- Step 19 的第一版在 `xfailed is None` 时重算；Step 23 跑 `testing/test_skipping.py` 后出现 `1 failed, 78 passed`，失败为非法 xfail 条件在报告阶段被再次求值。
- Step 24–28 放弃该版本，最终使用 `xfailed is None and xfailed_key in item._store`，明确区分“未曾成功写入缓存”和“正常缓存的 None”。
- Step 29 得到 `79 passed`；Step 30 主复现为 XFAIL；Step 32 fixture 动态添加 marker 的两例均为 XFAIL。
- Step 35–38 清理临时文件并核验只提交 `skipping.py` 的 patch。

这是最强的过程证据：不仅命中主问题，还通过失败反馈发现了隐藏的状态边界，并在相关测试文件全通过后收尾。其 `resolved=true` 与过程质量一致。

### 失败案例：`20260217_mini-v2.0.0_claude-4-6-opus.json`

- Step 13 在禁用 assertion rewrite 后成功复现主问题。
- Step 15 的最终思路为：只要 `xfailed is None`，就在 `pytest_runtest_makereport` 重算；Step 17 主复现变为 XFAIL。
- 该条件没有限制 call 阶段，也没有检查 `xfailed_key` 是否已存在。
- Step 19–28 多次运行相关测试但受到 Python 3.11 assertion-rewrite 和测试环境干扰；轨迹以“环境既有问题”解释大范围失败，未构造“非法 xfail 条件在 setup 报告时不得二次求值”的针对性回归测试。
- 最终 patch 保留无条件阶段重算，`resolved=false`。

该轨迹的定位和主复现较好，但验证没有覆盖其 patch 新增的报告阶段行为。Gemini 3.5 Flash 的 Step 23 提供了跨轨迹的直接佐证：正是这一类无边界重算会导致 `test_errors_in_xfail_skip_expressions` 失败。

### 反例：`20260226_mini-v2.0.0_gemini-3-pro-high.json`

该轨迹主复现和 `testing/test_skipping.py` 的 79 项均通过，但最终改为在每个 call report **无条件**重算。它还专门验证了动态 `append=False` 的 strict marker 会覆盖静态 marker。此行为超出“此前无 xfail、测试中动态加 xfail”的最小需求，改变了已有静态缓存的优先级语义；`resolved=false` 说明“本地 79 项通过”不足以证明 patch 与隐藏契约兼容。

## 逐轨迹诊断

| 轨迹 | `resolved` | 过程分 | 过程质量诊断 |
|---|---:|---:|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | true | 85 | 正确定位 makereport 缓存，最终仅在 call 且 `None` 时重算；验证主复现、XPASS 和 `--runxfail`。环境故障导致相关套件证据不完整。 |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | true | 87 | 读取历史并将条件限制为 call、`None`、非 `runxfail`；边界测试较多。存在冗长临时测试和部分预期 strict 失败的解释噪声，但最终范围稳健。 |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | false | 64 | 主复现已修复，但最终 `if xfailed is None` 缺少阶段/key 边界；相关套件失败没有转化为针对性回归假设。 |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | false | 60 | 一开始先改 `assertion/rewrite.py` 以绕过环境问题，后虽还原，工具选择偏离任务；最终在 makereport 无条件重算，扩大语义范围。 |
| `20260217_mini-v2.0.0_glm-5-high.json` | false | 67 | 历史调查和临时边界测试充足，也覆盖 `--runxfail`；最终仍采用无阶段保护的 `None` 重算，且验证受环境故障影响。 |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | true | 88 | 处理 `rep.when == "call"`、`runxfail` 和缓存为空，patch 最小；主复现与动态 XPASS 有真实结果。未跑完整相关套件是主要证据缺口。 |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | false | 43 | 先错误修改 call hook，后恢复；最终无条件重算。多次测试以 `|| true` 掩盖失败，缺少有效的端到端验证。 |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | false | 66 | 主复现、条件/静态/run=False 等样例较多，但最终仍是未限制阶段的 `None` 重算；相关套件没有形成可信通过证据。 |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | false | 61 | 查阅历史和 Store，但额外把 call hook 从“值为 None 时刷新”改为“key 不存在时刷新”，无必要且可能漏掉 fixture 阶段动态 marker；最终改动范围过大。 |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | false | 76 | 主复现、静态/动态组合和 79 项套件均有真实通过结果；但最终无条件刷新已有 xfail，主动改变静态与动态 marker 的优先级，隐藏评测未通过。 |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | true | 94 | 发现初版导致 `1 failed, 78 passed`，据此收紧为 key 存在保护；主复现、fixture、79 项相关套件和清理均完成，是最完整闭环。 |

## 可执行改进建议

1. 在修改前写出状态表：`runxfail`、`xfailed_key` 不存在、存在且为 `None`、存在且为 `Xfail`，并标明 setup/call/makereport 各阶段允许的求值动作。
2. 将验证最小集固定为：原始动态失败、`--runxfail`、静态 xfail、动态 strict/XPASS、fixture 动态 marker、非法 xfail 条件导致的 setup 错误。最后一项能直接拦截多数失败 patch。
3. 环境异常应先建立基线对照。若 Python 3.11 assertion-rewrite 不兼容，使用 `--assert=plain` 等受控手段，不应修改无关生产源码来让测试运行。
4. 禁止用 `|| true`、`head` 管道输出替代结果判断；每个关键测试应保留命令、退出码和预期结果。
5. 优先最小 patch。除非有明确回归测试，不改变 `pytest_runtest_call` 的既有缓存语义，也不要无条件覆盖已缓存的静态 xfail。
6. 评测侧应保留隐藏失败日志、统一 Python/依赖版本，并分别报告“主复现通过”“相关套件通过”“隐藏评测通过”，避免将单一 `resolved` 当作过程解释。

不能仅凭这些轨迹确定的事项包括：每个 `resolved=false` 对应的具体隐藏断言、Gemini 3 Pro 的唯一失败机制、模型之间固有能力排序，以及最终上游 patch 的精确文本。现有证据支持的是过程与结果的关联，以及上述生命周期边界是最可信的技术分界。
