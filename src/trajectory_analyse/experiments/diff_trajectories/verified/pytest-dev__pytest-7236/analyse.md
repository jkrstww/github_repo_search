# pytest-dev__pytest-7236 轨迹评测报告

## 一、评测范围与方法

本报告逐一解析了目录中的 11 个 ATIF-v1.7 JSON 轨迹，共 581 个步骤。评测依据包括：

- Agent 的任务理解与因果假设；
- 源码、历史提交和标准库行为的定位；
- 命令、返回码和测试输出；
- 最终提交的补丁；
- 错误后的恢复行为；
- `git diff`、`git status` 和提交协议；
- 用户给出的外部 `resolved` 标签。

共有 8 条 `resolved=true`、3 条 `resolved=false`。轨迹不包含外部 grader 的详细日志，因此 `resolved` 只能作为最终结果信号，不能反推出每一步是否正确。

## 二、问题本质

基线实现位于 `src/_pytest/unittest.py`：

1. 使用 `--pdb` 时，pytest 保存真实的 `TestCase.tearDown`；
2. 再把实例上的 `tearDown` 替换成空函数，以便推迟清理、保留调试现场；
3. pytest 的 teardown 阶段随后无条件调用保存的真实 `tearDown`；
4. 但 unittest 对装饰器跳过、`setUp` 失败或在 `setUp` 中跳过的测试，本来不会调用 `tearDown`。

因此，正确的不变量并不是简单的“测试最终是否 skipped”，而是：

> 只有 unittest 本身已经尝试调用替代后的 `tearDown`，pytest 才应在稍后的 teardown 阶段调用真实 `tearDown`。

这还能保留一个重要边界：在测试方法内部调用 `skipTest()` 时，unittest 会执行 `tearDown`。`20260901...gemini-3-5-flash.json` 的步骤 5–7 明确实验证明了这三种不同语义。

## 三、总体结论

### 1. 实现方案与外部结果

| 方案 | 轨迹 | 外部结果 | 评价 |
|---|---|---:|---|
| 记录 unittest 是否实际调用替代 `tearDown` | Gemini 3 Flash、GPT-5.2、Gemini 3.5 Flash | 3/3 成功 | 语义最完整，能区分装饰器跳过、`setUp` 失败和测试体内动态跳过 |
| 检查 `skipped_by_mark_key` 的布尔值 | Claude 4.5 Opus/Sonnet、GLM-5、MiniMax、Gemini 3 Pro | 4/5 成功 | 能修复题面复现，但可能错误抑制“测试体内 skip 后本应执行”的 `tearDown` |
| 预检查 unittest 类/方法 skip 装饰器 | Claude 4.6 Opus | 1/1 成功 | 对题面直接有效，但没有覆盖 `setUp` 失败或 `setUp` 中动态跳过 |
| 错误使用 Store API 或键存在性 | GPT-5 Mini、Kimi K2.5 | 0/2 成功 | 分别产生确定性 `TypeError` 和正常测试不执行延迟 `tearDown` 的回归 |

成功轨迹的主要共同点是：定位到了 `TestCaseFunction.runtest()` 与 `teardown()` 的跨阶段状态传递，并至少提交了针对该位置的小范围补丁。失败的 GPT-5 Mini 和 Kimi 则都在 pytest 自定义 `Store` API 上犯了可直接由源码发现的错误。

### 2. 过程质量与 `resolved` 并不一致

- GPT-5.2 没有获得任何补丁后的真实测试通过证据，但实现遵循“是否实际调用”的正确不变量，最终 `resolved=true`。
- MiniMax 主要依赖手写 mock 模拟，没有跑通真实复现，仍然 `resolved=true`。
- Gemini 3 Pro 跑通了复现、`testing/test_unittest.py`（50 passed, 9 skipped）、`testing/test_debugging.py`（20 passed, 37 skipped）和两个自建测试，最终补丁又与多个成功样本等价，却是 `resolved=false`。仅凭现有轨迹无法严谨解释该外部失败。
- 多个 `resolved=true` 的 skip-key 补丁对“测试方法内部调用 `skipTest()`”存在潜在过度修复，因此 resolved 也不能视作完整语义正确性的证明。

### 3. 环境是显著混杂变量

大多数 20260217/20260226 轨迹运行在 Python 3.10/3.11，旧版 pytest 的 assertion rewriting 因 `ast.alias` 缺少位置信息而失败。不同 Agent 的处理差异很大：

- Claude 4.6 使用 `--assert=plain`；
- GLM-5 和 Gemini 3 Pro 临时修改 assertion rewriting 后测试，再恢复；
- GPT-5.2 和 MiniMax 未能获得真实测试证据；
- 20260901 的 Gemini 3.5 Flash 使用兼容的 Python 3.9，测试路径明显更顺畅。

因此不能把成功率差异单独归因于模型能力。Agent 版本、日期、Python 环境和依赖状态均不一致。

## 四、可复用的 100 分轨迹评分细则

| 维度 | 权重 | 高分档 | 中分档 | 低分与主要扣分条件 | 可观察证据 |
|---|---:|---|---|---|---|
| 任务理解与因果模型 | 15 | 13–15：明确生命周期和边界状态 | 8–12：理解题面但边界不足 | 0–7：只按报错表面修改；混淆 skipped 与是否应 teardown | 推理文本、状态表、标准库实验 |
| 定位与假设验证 | 15 | 13–15：定位调用链并验证假设 | 8–12：找到文件但因果验证有限 | 0–7：盲改、多处无关搜索、未读相关 API | 源码读取、历史提交、调用点搜索 |
| 工具与实验设计 | 10 | 9–10：命令精确、保留真实返回码 | 6–8：有效但有冗余 | 0–5：大量环境折腾、`|| true` 掩盖失败、破坏性全局安装 | 命令、返回码、步骤数 |
| 修改正确性与通用性 | 25 | 22–25：保持完整 unittest 语义 | 14–21：修复题面但存在边界风险 | 0–13：确定性异常、明显回归、错误 API；致命错误最高 5 分 | 最终 diff、API 签名、状态不变量 |
| 测试与验证证据 | 20 | 17–20：失败基线、修复后复现、反例和回归套件 | 9–16：只有部分真实测试 | 1–8：仅 mock/静态检查；0：修改后未执行 | pytest 输出、返回码、通过/失败计数 |
| 错误恢复 | 10 | 9–10：准确分类环境错误与代码错误并复验 | 5–8：能恢复但绕路 | 0–4：忽略失败、误归因、已出现反证仍提交 | 连续步骤中的诊断和修正 |
| 收尾与提交卫生 | 5 | 5：仅目标源文件、检查 diff/status、正确提交 | 3–4：基本完成但证据不全 | 0–2：遗留无关文件、空 patch、未提交或违规文件进入补丁 | `git status`、patch 内容、最终命令 |

总分档位：A 为 90–100，B 为 80–89，C 为 70–79，D 为 60–69，F 为 0–59。该评分刻意让“实现正确性 + 验证证据”占 45%，因为代码任务不能仅凭流畅推理得高分；同时保留 30% 给理解和定位，以区分偶然命中与可复用的问题解决能力。

## 五、逐条轨迹诊断

下表分项顺序为“理解/定位/工具/修改/验证/恢复/收尾”。

| 轨迹 | resolved | 过程评分 | 诊断摘要 |
|---|---:|---:|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | true | 86（14/14/6/21/17/9/5） | 找到引入延迟 teardown 的历史提交和 skip store；真实环境修复较曲折，但最终做了 skipped、正常和失败场景验证。补丁对动态 skip 有过度抑制风险。 |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | true | 88（14/14/7/21/18/9/5） | 步骤 11 复现“1 skipped, 1 error”，步骤 31 修复后仅 skipped，并覆盖通过、失败、类级 skip 等情况。过程证据较强，但采用 skip-key 方案。 |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | true | 83（14/14/7/20/15/8/5） | 读取 `unittest.TestCase.run` 后预检查类和方法装饰器；题面和类级 skip 均通过。多次改错再回滚，完整套件受 AST 兼容问题阻塞，方案未覆盖 `setUp` 失败。 |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | true | 89（15/14/8/22/17/8/5） | 正确转向“unittest 是否调用替代 tearDown”的信号，验证了失败测试和 `setUp` 失败。缺点是未调用时没有清空 `_explicit_tearDown`，可能延长绑定对象生命周期。 |
| `20260217_mini-v2.0.0_glm-5-high.json` | true | 84（13/13/4/21/20/8/5） | 93 步，效率最低；临时修补无关 AST 兼容代码后获得复现、既有 PDB 测试及全文件 50 passed/9 skipped，并在提交前恢复无关文件。验证强，但环境操作噪声大且方案仍偏题面化。 |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | true | 75（15/14/6/25/3/7/5） | 最终补丁通过替代函数记录 unittest 的真实调用意图，语义上属于最强方案；但依赖和 AST 问题后直接修改并提交，没有任何补丁后运行证据。 |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | false | 38（12/12/4/2/0/3/5） | 步骤 16 写成 `store.get(skipped_by_mark_key)`，而 pytest `Store.get` 必须传 `default`，会在 teardown 确定性抛出 `TypeError`。没有补丁后测试；外部失败与代码缺陷高度一致。 |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | false | 50（13/13/5/5/5/4/5） | 步骤 39 真实发现缺少 default 的 `TypeError`，但步骤 42 改成“键不存在”。该键通常已以 `False` 预置，导致正常测试也跳过真实 `tearDown`。现有验证只检查测试通过，没有可靠断言 teardown 副作用。 |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | true | 72（13/13/5/21/8/7/5） | 修改为 `.get(key, False)`，静态逻辑正确且提交干净；真实 pytest 始终被 AST 问题阻塞，步骤 35/59 只是复制逻辑的 mock，并不能证明集成行为。 |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | false | 85（14/14/5/21/18/8/5） | 临时修复 AST 后，复现、完整 unittest/debugging 文件及自建测试均通过，最终只提交 `.get(key, False)` 补丁。过程与多个成功样本等价；轨迹内没有足以解释 `resolved=false` 的证据。 |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | true | 96（15/15/9/25/19/8/5） | 最完整地建立 unittest 行为真值表；步骤 11 复现失败，步骤 18 使用真实调用信号，步骤 21 修复复现，步骤 22 验证 `setUp` 失败不调用 teardown，步骤 23 为 50 passed/9 skipped。debugging 文件另有两个环境相关失败。 |

## 六、具体案例

### 成功案例：Gemini 3.5 Flash

`20260901_mini-v2.4.2_gemini-3-5-flash.json` 没有直接把“skipped”当作单一状态：

- 步骤 5：装饰器跳过时不执行 `setUp` 和 `tearDown`；
- 步骤 6：测试方法内部 `skipTest()` 后仍执行 `tearDown`；
- 步骤 7：`setUp` 内 `skipTest()` 时不执行 `tearDown`；
- 步骤 11：确认基线在 `--pdb` 下产生 “1 skipped, 1 error”；
- 步骤 18：让临时 `tearDown` 只记录 unittest 是否调用过它；
- 步骤 21–23：复现通过，`setUp` 失败行为正确，完整 unittest 测试为 50 passed/9 skipped。

这些证据同时验证了根因、正例、反例和回归面，因此其成功不是仅靠补丁形状偶然命中。

### 失败案例：GPT-5 Mini

`20260217_mini-v2.0.0_gpt-5-mini.json` 的高层方向接近正确，但执行层存在致命缺陷：

- 步骤 11–12 因 pytest/attrs 缺失，未获得失败基线；
- 步骤 13 首次补丁命令失败；
- 步骤 16 最终写入 `store.get(skipped_by_mark_key)`；
- pytest 的 `Store.get(self, key, default)` 不提供默认参数，因此这不是普通字典 API；
- 步骤 17 后没有运行任何测试，步骤 20 只检查了 patch 文本。

因此“理解到要检查 skip”不能抵消未验证的 API 假设；`resolved=false` 有直接、确定且充分的代码证据。

### 异常案例：Gemini 3 Pro

`20260226_mini-v2.0.0_gemini-3-pro-high.json` 的步骤 49、51、54、56、57 分别给出了目标复现、完整 unittest/debugging 文件和新增回归测试的成功结果。最终补丁与四条 `resolved=true` 的 skip-key 补丁语义一致。

所以目前只能得出“外部标签与轨迹内证据不一致”，不能严谨声称它失败于代码逻辑。需要 grader 日志、实际应用后的工作树、测试选择器和提交接收记录才能继续归因。

## 七、可执行改进建议

1. 修改前先建立 teardown 真值表：装饰器 skip、`setUp` skip/失败、测试体内 skip、pass、fail；优先跟踪 unittest 的真实调用意图。
2. 强制执行“失败基线 → 修改 → 同命令通过”，禁止用 `|| true`、管道或 `head` 掩盖真实退出码。
3. 使用项目自定义容器前先读 API 签名；本题至少应验证 `Store.get(key, default)` 以及“键存在”和“值为 True”的区别。
4. 回归测试必须观测 `tearDown` 的副作用，而不能只看 pytest 显示 passed。
5. 最低验证集应包括题面复现、`test_pdb_teardown_called`、`testing/test_unittest.py`，以及测试体内 `skipTest()` 的反例。
6. 优先选择兼容解释器或 `--assert=plain`；如必须临时修补环境兼容代码，应在测试后恢复并记录测试受该补丁影响的范围。
7. 避免全局 `pip install`、`git add -A` 和在仓库中遗留临时测试；提交前检查只剩目标源文件。
8. 评测系统应保存 grader 测试名、stdout/stderr、补丁应用结果和最终工作树摘要，以解释 Gemini 3 Pro 这类标签冲突。

## 八、不能仅凭这些轨迹确定的结论

- 不能确定 Gemini 3 Pro 外部失败的具体原因；
- 不能据单样本断言某个模型系列整体优于另一系列；
- 不能分离模型、mini-swe-agent 版本、日期、Python 版本和依赖环境的影响；
- 不能确认所有成功补丁在动态 skip、Twisted、异步 unittest 和不同 Python 版本上都完全正确；
- 不能把较多步骤、较高 token 或较高成本视为更高质量：本样本中步骤数从 21 到 93，和 resolved 没有单调关系；
- 不能把 `resolved=true` 等同于最佳工程实现，也不能把 `resolved=false` 自动等同于低质量过程。
