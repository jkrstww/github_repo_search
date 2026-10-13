# 评测范围与口径

目录中共有 11 条 ATIF 轨迹，均处理 xarray issue #4687：`xr.where` 的属性保留问题。`resolved` 标签采用用户提供的 `compare.json` 映射，不把它当作过程正确性的替代指标。

成功 3 条，失败 8 条，成功率为 27.3%。轨迹步数从 23 到 149 不等，模型、agent 版本、运行日期和依赖环境也不一致。

# 总体结论

成功轨迹的共同点：

- 能定位 `xarray/core/computation.py::where`，并继续追踪 `apply_ufunc`、`apply_dataarray_vfunc`、`apply_variable_ufunc` 和 `merge_attrs`。
- 理解 `apply_ufunc` 默认以第一个 xarray 参数决定属性，发现 `cond` 通常是比较结果、属性为空，因此直接传 `keep_attrs=True` 可能仍然丢失 `x` 或 `y` 的属性。
- 增加 `keep_attrs` 接口，并至少验证显式 `True/False`、全局 `set_options`、DataArray 或 Dataset 输入。
- 生成仅包含目标源码文件的 patch，并完成最终提交命令。
- 有效成功并不等于覆盖所有语义：成功的 gpt-5.2 轨迹仍显示第一组“属性只存在于比较源数据、而 `x/y` 为标量”的案例返回 `{}`。

失败轨迹的共同点：

- 多数只验证了“属性位于 `x` 或 `y`”的案例，没有解决“属性位于用于构造 `cond` 的原始 DataArray”这一更严格情形。
- 把“自定义 `keep_attrs` 后测试通过”误认为完整修复，忽略了原始 MCVE 仍失败。
- 一些轨迹默认保持旧行为（`_get_keep_attrs(default=False)`），这只能提供可选参数，不能满足无参数调用的属性保留期望。
- 多次出现依赖不兼容、错误命令、脆弱的 `sed`/脚本替换和长时间重复尝试；步数多不代表验证更充分。
- 测试常集中于自写脚本，缺少与原始 issue 完全一致的回归测试，或完整测试被 pandas/NumPy 环境问题阻断。

模型设置与结果不是单调关系：gpt-5.2、Claude Opus 4.6 和新版 Gemini 3.5 Flash 成功；Minimax、Gemini 3 Pro、GLM-5 等即使步数很多仍失败。因此，语义假设、验证闭环和命令可靠性比“高档位”或 token 数更能解释结果。模型、agent 版本和环境同时变化，不能据此得出单独的模型因果结论。

# 可复用轨迹评分细则（100 分）

| 维度 | 权重 | 高档 | 中档 | 低档与扣分条件 | 可观察证据 |
|---|---:|---|---|---|---|
| 任务理解与验收标准 | 15 | 同时覆盖两个 MCVE、可选参数、默认行为和 dtype 问题 | 覆盖主要属性场景但遗漏一个验收点 | 忽略原始 MCVE、把 `resolved` 当作唯一正确性；扣 3–8 分 | 是否明确区分 `cond`、`x`、`y` 属性来源 |
| 定位与假设 | 15 | 追踪 `where → apply_ufunc → merge_attrs`，验证假设 | 定位到 `where` 和 `apply_ufunc`，但未分析参数顺序 | 仅凭函数名猜测或未验证属性合并策略；扣 3–8 分 | 搜索源码、读取 `merge_attrs`、解释 first-object 行为 |
| 工具与命令选择 | 10 | 命令短、可复现、作用域准确 | 有少量重试但最终可控 | 反复 malformed shell、脆弱文本替换、误改无关代码；每类扣 1–5 分 | 返回码、命令是否针对目标文件 |
| 实现正确性与范围 | 25 | 正确处理默认、`True/False`、合并策略、DataArray/Dataset/Variable，且只改目标源码 | 主路径正确但遗漏条件属性、变量级属性或冲突策略 | 原始案例仍失败、默认语义错误、误改 `dot` 等无关代码；扣 5–15 分 | 最终 diff、参数映射、结果属性 |
| 测试与验证证据 | 20 | 原始 MCVE、边界场景、目标 pytest 和尽可能完整测试均通过 | 有针对性测试，但覆盖不完整或环境阻断部分测试 | 只运行自写 happy path，或已知失败仍声称完成；扣 3–10 分 | pytest 输出、断言、失败原因 |
| 错误恢复与迭代 | 10 | 能区分环境故障与代码故障，并调整方案 | 最终恢复但有明显重复 | 依赖错误后无诊断、循环尝试、放弃关键验证；扣 1–6 分 | NumPy/pandas/pytest 错误后的处理 |
| 最终收尾与证据链 | 5 | patch 仅含目标文件、检查 diff、完成提交 | patch 存在但检查不充分 | 无 patch、含辅助文件或未最终验证；扣 2–5 分 | `git diff`、`patch.txt`、提交命令 |

这样设计是因为本任务的主要风险不是“能否编辑一行代码”，而是属性来源、默认策略和 `apply_ufunc` 参数顺序的语义错误；因此实现正确性和测试证据合计占 45%，过程质量占 50%，收尾占 5%。评分应独立于外部 `resolved` 标签，标签只用于事后比较。

# 具体案例

## 成功案例：`20260217_mini-v2.0.0_gpt-5-2-high.json`

关键事件：

- 第 3 步先搜索 `where` 和相关实现。
- 第 4、6、8 步发现缺少 NumPy、pandas 以及 NumPy 2.x 的 `np.unicode_` 兼容问题，并通过安装 pandas、降级 NumPy 恢复运行。
- 第 12 步以后继续检查 `where`、`apply_ufunc` 和属性合并逻辑。
- 第 28 步加入 `keep_attrs`，并在属性保留时调整参数顺序，使 `x/y` 优先于通常没有属性的 `cond`。
- 第 29 步验证 `where(da==0, -1, da)` 得到 `{'foo': 'bar'}`，`keep_attrs=False` 得到 `{}`。
- 第 33 步目标测试通过，第 38–40 步只生成 `xarray/core/computation.py` 的 patch 并提交。

这支持“成功需要同时解决接口和参数顺序”的结论。但第 29 步也显示 `where(da==1, 5, 0)` 仍为 `{}`，因此该轨迹并未证明所有原始语义都已解决；`resolved=true` 只能说明外部评测接受了它覆盖的行为。

## 成功案例：`20260901_mini-v2.4.2_gemini-3-5-flash.json`

关键事件：

- 第 23–25 步直接比较 `keep_attrs="override"` 和 `"drop_conflicts"`，确认第一个策略会被空的 `cond.attrs` 覆盖，而后者能保留 `x/y` 属性。
- 第 46–53 步测试 callable 属性策略，先发现布尔值映射错误，又通过打印 `variable_attrs` 发现条件属性与数据属性不能靠对象身份区分。
- 第 53 步改为先执行 `apply_ufunc(..., keep_attrs=False)`，再根据 `x/y` 类型和 Dataset 变量手动合并属性。
- 第 78 步曾出现 `NoneType` 属性赋值错误，第 79–80 步用 `or {}` 修复。
- 第 87 步运行 `test_computation.py`，结果为 247 passed、1 skipped；第 88–90 步检查并提交 patch。

这条轨迹的过程证据最完整，且覆盖 Dataset、DataArray、Variable、冲突属性和坐标属性。不过手动属性重建逻辑较复杂，仍需要专门的回归测试防止变量级属性错配。

## 失败案例：`20260217_mini-v2.0.0_claude-4-5-opus-high.json`

关键事件：

- 第 12–15 步解决依赖问题并复现属性丢失。
- 第 19 步加入 `keep_attrs=True` 后，两个示例仍返回 `{}`。
- 第 22–24 步正确发现：`data == 1` 产生的 `cond` 没有属性，`apply_ufunc` 的默认 `override` 又只取第一个 DataArray。
- 第 31 步改用 `"drop_conflicts"`，第 38–39 步的分支属性和边界测试通过。
- 但第 39 步明确断言“默认行为应该不保留属性”，与原始 issue 的无参数预期不一致；第 32–35 步也显示条件属性来源的案例仍为 `{}`。

因此，这条轨迹定位能力不错，但最终假设错误：它把“默认丢弃属性”当成兼容性要求，并未真正闭合原始验收标准，最终 `resolved=false` 与过程证据一致。

# 逐条诊断摘要

| 轨迹文件 | 过程质量摘要 | 评分 | resolved |
|---|---|---:|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | 能复现并分析 `merge_attrs`，但默认语义和条件属性案例仍错误；目标测试有限 | 76 | false |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | 后期实现较完整，显式/全局测试通过；早期依赖问题，完整测试受 pandas 阻断，第一 MCVE 仍失败 | 81 | false |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | 35 步内完成，手动从 `x`/`y` 取属性，目标 pytest 通过；完整测试受 pandas 兼容性阻断 | 84 | true |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | 缺少解释，频繁使用脆弱 `sed`；多次复现第一案例失败，最终仅解决部分路径 | 58 | false |
| `20260217_mini-v2.0.0_glm-5-high.json` | 124 步，出现大量 `127` malformed shell 命令；最终 patch 仅透传 `keep_attrs`，默认仍不保留 | 63 | false |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | 定位清晰，依赖恢复有效，调整参数顺序并验证目标 pytest；未覆盖所有属性来源 | 93 | true |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | 仅 23 步，patch 命令失败且缺少 NumPy，无法运行最终复现；最终只有一行透传改动 | 45 | false |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | 对 `merge_attrs` 分析较好，环境恢复后显式分支通过；默认仍为 false，第一案例失败 | 71 | false |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | 调试和测试很充分，但曾误改 `dot` 后再回滚；自定义测试把默认丢属性定义为正确 | 85 | false |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | 能发现 `override` 问题并尝试 `where_method`；多次重写，最终默认策略和条件属性仍不满足 | 70 | false |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | 源码追踪、失败诊断、Dataset/变量级测试和完整计算测试最完整；实现复杂但外部评测通过 | 91 | true |

# 可执行改进建议

- 先把验收矩阵写成四组：属性在 `cond`、只在 `x`、只在 `y`、同时在 `x/y`；每组分别测试默认、`True`、`False` 和全局选项。
- 明确 `keep_attrs=True` 的语义是保留哪些输入的属性，不能只依赖 `apply_ufunc` 的第一个参数。
- 对 `Dataset` 增加顶层 attrs 和每个 data variable attrs 的独立断言。
- 将原始 MCVE 直接变成回归测试，避免用“改写后的示例”替代真实问题。
- 对 `merge_attrs` 的 `override`、`no_conflicts`、`drop_conflicts` 做显式选择，并记录冲突时应保留还是丢弃。
- 运行测试前固定兼容环境，至少锁定 NumPy、pandas 和 pytest 版本；环境失败时要区分“代码失败”和“收集阶段失败”。
- 避免全局 `sed` 和未验证的 heredoc；修改后立即检查 diff，确认没有误改 `dot` 或其他函数。
- 最终提交前必须重新运行原始 MCVE、目标测试、边界测试，并把已知失败列入结论，而不是仅报告自写脚本通过。

# 不能仅凭这些轨迹确定的结论

- 不能证明某个模型本身一定优于其他模型，因为模型版本、agent 版本、日期、依赖和随机状态同时变化。
- 不能证明 `resolved=true` 代表所有边界行为正确；成功轨迹仍有未解决或未覆盖的案例。
- 不能判断 dtype 问题是否被外部评测检查；多数轨迹只验证 attrs，没有修复 NumPy 标量导致的类型提升。
- 不能据此评价代码长期维护性、性能、Dask 延迟执行行为或未运行测试模块的兼容性。
- 不能把 pandas/NumPy 导入错误归因于模型实现错误；多条轨迹明确显示这是旧版 xarray 与新依赖的兼容问题。
- 不能从单次轨迹推断模型在其他仓库、其他 issue 或重复运行中的稳定成功率。
