# Matplotlib #20826 多模型轨迹评测报告

## 一、评测范围与方法

目录中共有 10 个有效的 ATIF-v1.7 JSON 轨迹，均已逐步检查，包括用户请求、模型推理、55–137 次不等的工具调用、命令输出、最终补丁及提交前验证。标签分布为：

- `resolved=true`：3 条
- `resolved=false`：7 条
- `20260901_mini-v2.4.2_gemini-3-5-flash.json` 不在当前目录，未纳入评价。

过程评分独立于 `resolved`：外部结果只表示评测器最终是否接受补丁，不直接证明某个分析、测试或实现步骤正确。

## 二、总体结论

所有较完整的轨迹最终都定位到同一因果链：`Axes.clear()/cla()` 调用 `Axis.clear()`，后者执行 `_reset_major_tick_kw()` 和 `_reset_minor_tick_kw()`，将原有 tick 参数清到只剩 `gridOn`。这既丢失共享子图设置的 `label1On/label2On`，也可能丢失默认的 `tick1On/tick2On`，产生两类症状：

1. 共享轴内部 tick label 被重新显示。
2. 默认应关闭的 top/right ticks 出现。

不同实现路线及外评结果如下：

| 实现路线 | 轨迹 | resolved | 评价 |
|---|---|---:|---|
| `clear()` 后重新执行 `_label_outer_*axis()` | Claude 4.5 Opus、MiniMax | 2/2 成功 | 补丁最小，符合外评重点，但不一定完整解决非共享轴 top/right ticks |
| 在 `Axis` reset 中只保留四个可见性字段 | GLM-5 | 1/1 成功 | 同时覆盖 label 与 tick 方向，且避免保留字体、旋转等全部样式，语义最有针对性 |
| 重施 rcParams，再重施 outer-label 规则 | Sonnet 4.5、Claude 4.6、Gemini Flash、Gemini Pro | 0/4 成功 | 主复现多数通过，但生命周期改动更广，部分遗漏 minor ticks；外评拒绝的确切用例不可见 |
| 保存并恢复整个私有 tick 参数字典 | GPT-5.2、GPT-5-mini、Kimi | 0/3 成功 | 把“必须保留的共享布局状态”扩大为“保留所有 tick 样式”，与 `clear()` 的重置语义存在冲突 |

成功轨迹的主要共性是：均得到过可运行的修后证据，并最终采用较窄的共享标签修复或选择性可见性保留。失败轨迹中，GPT-5.2 和 GPT-5-mini没有获得任何修后运行证据；Kimi、Sonnet 等虽有较多测试，但实现语义或覆盖范围仍可能与隐藏测试不一致。

不过，这一相关性不是充分条件。Sonnet 4.5 的过程证据比 MiniMax 更完整，却是 `resolved=false`；MiniMax 明知 top ticks 仍有问题却是 `resolved=true`。因此当前标签更像是对特定隐藏测试集合的命中情况，不能视为完整 bug 修复质量。

证据置信度分层如下：

- **强证据**：最终 diff、明确的异常栈、修前/修后私有状态及 tick artist 可见性、带断言的测试结果。
- **中等证据**：自定义脚本输出、选择性 pytest、git 历史中的回归提交。
- **弱证据**：模型自己打印“All tests passed”、只生成图片但未检查图片、被管道掩盖的退出码。
- **缺失证据**：外部评测失败日志、隐藏测试内容、参考补丁，以及多数轨迹的最终视觉产物。

## 三、可复用的 100 分轨迹评分细则

| 维度 | 权重 | 满分所需的可观察证据 | 主要扣分条件 |
|---|---:|---|---|
| 任务理解与验收准则 | 12 | 区分共享标签、top/right ticks、轴仍保持联动三个要求；明确 `clear()` 应保留和重置什么 | 漏掉一种症状 -3；没有定义预期矩阵 -3；把 `resolved` 当正确性证明 -4 |
| 定位与因果分析 | 18 | 展示 `Axes.cla → Axis.clear → _reset_*_tick_kw` 调用链；比较 clear 前后状态；结合回归提交或基线 | 仅凭文件名猜测 -8；只修表象、不解释状态来源 -5；主轴 `_sharex=None` 等关键假设未验证 -3 |
| 工具与实验设计 | 12 | 定向检索、正确依赖环境、红绿对照、命令退出码可靠，避免污染运行环境 | 错 API/无效命令 -2；反复安装不兼容依赖 -2；`pytest | head/tail` 掩盖失败 -4；混用系统包和源码包 -4 |
| 实现正确性与范围 | 28 | 同时考虑 major/minor、共享/非共享、初始化时序和 projection；修改小且符合 `clear()` 契约 | 保留不应保留的全部样式 -6；遗漏一类症状 -6；遗漏 minor -4；修改生命周期/API 过广 -4；无关代码进入补丁 -3 |
| 测试与验证证据 | 22 | 修前失败、修后通过；断言标签和 tick line；测试共享矩阵、单轴、重复 clear、polar/twin；至少一组现有测试 | 完全无运行验证 -14；只有打印无断言 -4；无现有测试 -4；忽略反例 -8；只验证中间态而非最终补丁 -4 |
| 错误恢复 | 5 | 能区分环境错误和实现错误，修复后重新验证，不重复无效路径 | 忽略异常 -3；错误归因或重复失败命令 -2 |
| 收尾与证据卫生 | 3 | 最终 patch 仅含目标源码，检查 diff/status，诚实报告未通过项 | 遗留无关源码改动 -2；把失败说成全通过 -2；未检查最终 patch -1 |

单维度按完成比例取 `100% / 75% / 50% / 25% / 0%` 五档。总分建议解释为：90–100 卓越，75–89 较强，60–74 基本可用，40–59 高风险，0–39 不可信。步数、token、成本和 `resolved` 不直接计分，避免奖励冗长操作或结果泄漏。

## 四、关键案例

### 案例 A：GLM-5，成功且因果证据最完整

文件：`20260217_mini-v2.0.0_glm-5-high.json`

- S47 明确显示共享轴的 `_major_tick_kw` 从完整的 `tick1On/tick2On/label1On/label2On` 退化为只剩 `gridOn`。
- S59–S60 找到引入 `_reset_*_tick_kw()` 的 grid visibility 回归提交，定位具有历史依据。
- 最终补丁只在 major/minor reset 时保留四个可见性键，而不是保留字体、旋转等所有样式。
- S63–S65 验证共享标签、默认 ticks、grid 和非可见性样式重置；S91 又检查轴联动、重复 clear 和 top/right ticks。

这些证据支持其 `resolved=true`。不足是补丁夹带了 `np.Inf → np.inf`，且 S90 显示大量未清理的测试文件；pytest 也曾因依赖问题无法正常收集。因此它是实现思路最强，而不是收尾最干净的轨迹。

### 案例 B：GPT-5.2，失败主要源于无验证和过度保存状态

文件：`20260217_mini-v2.0.0_gpt-5-2-high.json`

- 静态分析正确识别 `_major_tick_kw/_minor_tick_kw` 被清空。
- S28–S36 始终无法运行本地 Matplotlib，尝试复制 Python 3.8 的 `.so` 到 Python 3.11 环境也未解决导入。
- S41 只执行了 `py_compile`，没有任何行为测试。
- 最终补丁复制并恢复四个完整私有字典，会一并保留 `labelsize`、rotation 等可能应由 `clear()` 重置的状态。

因此过程无法证明修复有效，且实现扩大了行为变更范围。`resolved=false` 与这些风险一致，但没有外评日志，不能断言具体是哪一项隐藏测试失败。

### 案例 C：MiniMax 成功标签暴露了外评覆盖局限

文件：`20260217_mini-v2.0.0_minimax-2-5-high.json`

- S37–S40 正确证明共享标签丢失，并发现主轴 `_sharex=None`、必须检查 sibling group。
- 最终补丁只在 `clear()` 后重施 `_label_outer_xaxis/_label_outer_yaxis`，共享标签结果正确。
- 但 S86、S112、S119 均显示 `tick2line` 在 clear 后由 `False` 变成 `True`；模型最终仍未修复该问题。
- S136 的最终断言只覆盖共享标签，没有覆盖已经发现的 top ticks 反例。

尽管 `resolved=true`，该轨迹并未完整解决 bug 报告中的第二个症状。这是“外部通过不等于完整正确”的最直接证据。

### 案例 D：Sonnet 4.5 说明高过程质量也不保证外评通过

文件：`20260217_mini-v2.0.0_claude-4-5-sonnet-high.json`

它检查了回归历史和 polar 语义，识别主轴 sibling 问题，并在 S49、S54、S77–S80、S88 分别验证共享标签、单轴 top/right ticks、非共享轴和 polar。主场景均显示正确，但最终仍为 `resolved=false`。可见风险是补丁在每次 clear 后重复应用两套 rcParams 并加入 rectilinear 分支，范围大于成功的最小补丁；同时 S87 的 pytest 实际是 collection error，只因 `| head` 而返回 0。缺少外评日志时，不能进一步确定隐藏失败点。

## 五、逐轨迹诊断

| 轨迹 | resolved | 过程分 | 过程质量与最终结果诊断 |
|---|---:|---:|---|
| Claude 4.5 Opus | true | 82 | 定位准确；首次修改触发 `_subplotspec` 初始化异常后及时加 guard，并检查实际 label artist。现有测试有 27 pass，但仍有 2 个环境相关失败；补丁较小，top/right 单轴证据不够强。 |
| Claude 4.5 Sonnet | false | 82 | 过程系统、考虑 polar 和 sibling group，主复现及自定义边界均通过。补丁重复应用 rcParams，官方测试未真正完成；外评拒绝的具体原因不可从轨迹确定。 |
| Claude 4.6 Opus | false | 80 | 基线比较和错误恢复较好，得到 29 passed。新增 `_tick_params_from_rc`、基类空 hook 和子图 guard，修改面偏大；axes/figure 测试仍受 NumPy 2.0 阻断。 |
| Gemini 3 Flash | false | 67 | 能复现并修到共享标签和单轴 top tick 输出正确，但把初始化中的整块 tick 配置移进 `cla()`，改变调用时序；无 pytest，仅少量打印型测试。 |
| GLM-5 | true | 87 | 因果定位、选择性状态保存和边界验证最强；同时修 major/minor。扣分来自无关 `np.inf` 修改、未清理文件和部分被环境阻断的测试。 |
| GPT-5.2 | false | 53 | 静态定位合理，但本地运行从未成功；只有语法编译。恢复全部私有 tick 状态过宽，缺少任何修后行为证据。 |
| GPT-5-mini | false | 36 | 没有成功导入 Matplotlib；首次补丁命令失败。通过宽泛 `except Exception: pass` 吞掉恢复错误，并把内部 kwargs 交给公开 API；最终完全未验证。 |
| Kimi K2.5 | false | 76 | 查看了回归提交并做了大量自定义测试；但把“自定义 labelsize/rotation 应在 clear 后保留”当作正确 oracle，最终保存完整字典。还混用了已安装 Matplotlib 与源码树，官方 pytest 未真正运行。 |
| MiniMax 2.5 | true | 72 | 最终共享标签补丁小且外评通过，覆盖 sharex/sharey、twin 和重复 clear；过程非常迂回，多次语法错误，并明确发现却忽略 top/right ticks 仍错误。 |
| Gemini 3 Pro | false | 65 | 最初复现同时捕获两类症状，并修正初始化阶段 `_subplotspec` 异常。最终只为 major ticks 恢复 rc 默认，遗漏 minor；没有 pytest，且最终重建补丁后未重新执行完整验证。 |

## 六、可执行改进建议

1. 编辑前建立明确的行为矩阵：major/minor × 共享/非共享 × label/tick line × top/bottom/left/right，并明确哪些用户样式在 `clear()` 后应重置。
2. 用同一断言脚本比较回归前提交、当前基线和候选补丁，避免凭直觉决定“保存全部状态”还是“恢复 rc 默认”。
3. 优先修复状态丢失的最小来源；若保留字典，只保留经契约确认必须保留的键，禁止宽泛 `except Exception`。
4. 固定兼容依赖，例如本项目使用 `numpy<2`、兼容版本的 pyparsing；不要安装最新版 Matplotlib 后与源码树混用。
5. 测试必须使用断言检查 `get_major_ticks()` 中的 `label1/label2` 和 `tick1line/tick2line`。仅看 `get_xticklabels()` 的空列表可能把“没有返回标签”误判为“标签正确隐藏”。
6. pytest 不应直接接 `head/tail`；使用 `set -o pipefail`，或保存完整日志后单独截取展示，确保退出码可信。
7. 至少运行：原始复现、单轴 top/right、sharex/sharey 各模式、minor ticks、polar、twin、重复 clear、`tick_params(reset=True)` 和相关现有测试。
8. 清理后重新运行最终 patch，而不是只验证中间版本；提交前检查 `git diff` 与 `git status`，并如实列出未通过测试。

## 七、不能仅凭这些轨迹确定的事项

- 无法确定 7 条失败轨迹各自命中的具体隐藏测试或外评错误，因为没有 evaluator 日志和参考补丁。
- 无法仅凭 10 个单任务样本形成可靠的模型能力排名；模型版本、成本、环境状态和探索长度均不同。
- 无法确认图片在所有 backend、Matplotlib 版本和交互环境中的视觉一致性。
- `resolved=true` 不能证明完整覆盖 bug 描述，MiniMax 的 top/right ticks 反例已经直接否定这一推论。
- `resolved=false` 也不能证明全部实现思路错误；Sonnet 4.5 和 Claude 4.6 的主复现及多项测试实际通过。
- 缺失的 `20260901_mini-v2.4.2_gemini-3-5-flash.json` 没有任何可审计内容，不能据标签或文件名推断其表现。
