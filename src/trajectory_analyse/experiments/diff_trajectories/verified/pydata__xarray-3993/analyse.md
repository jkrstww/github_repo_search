# pydata/xarray-3993 多模型轨迹评测报告

## 一、评测范围与任务基准

共检查 11 个 ATIF-v1.7 JSON 轨迹文件，逐步阅读了用户任务、模型推理、工具调用、命令输出、最终补丁和提交动作。外部标签为成功 8 条、失败 3 条，成功率 72.7%。

任务的核心契约是：

- 将 `DataArray.integrate` 的主参数从 `dim` 改为 `coord`。
- 保持其与 `Dataset.integrate`、两类 `differentiate` API 一致。
- 正确转发 `coord` 和 `datetime_unit`。
- 是否保留旧 `dim` 需要明确处理；若保留，应有一致的冲突检查和可见告警。
- 修改范围原则上只应涉及 `xarray/core/dataarray.py`。

## 二、总体结论

成功轨迹的最稳定共性不是步骤更多或推理更长，而是最终补丁同时满足了三个条件：

1. 修改范围集中在 `DataArray.integrate`。
2. `coord` 能正确转发到 `Dataset.integrate`。
3. 若兼容旧 `dim`，使用 `FutureWarning`，并拒绝同时传入 `coord` 和 `dim`。

8 条成功轨迹最终都只提交了 `xarray/core/dataarray.py`，并都使用 `FutureWarning`。多数还验证了新关键字、旧关键字、位置参数和冲突参数。相比之下，三条失败轨迹分别暴露出不同风险：

- `gpt-5-mini`：没有拒绝同时提供 `coord` 和 `dim`，此时会静默忽略 `dim`；使用默认被过滤的 `DeprecationWarning`，且修改后完全没有成功运行 xarray。
- `gemini-3-flash-high`：把本来已经正确的 `Dataset.integrate` 也扩展为接受 `dim`，形成非任务要求的 API 扩张，并同样使用 `DeprecationWarning`。
- `gemini-3-pro-high`：核心行为测试通过，但最终补丁在 `return` 后直接开始 `def unify_chunks`，缺少类方法间空行，存在明确的静态检查失败风险；它没有运行格式或 lint 检查。

测试强度与 `resolved` 并非一一对应。`gemini-3-pro-high` 的目标测试为 2 passed，仍然失败；`minimax-2-5-high` 只做了语法检查和脱离项目的模拟逻辑，却成功。这说明外部评测很可能还覆盖了轨迹没有直接展示的契约或静态质量条件。

成功样本也并非过程无缺陷：

- `claude-4-5-sonnet-high` 把第二个位置参数从 `datetime_unit` 变成 `dim`。
- `kimi-k2-5-high` 将 `datetime_unit` 改成仅限关键字。
- `glm-5-high` 没有显式处理缺少 `coord` 的情况，并写入未经证实的“自 0.12.0 起弃用”。
- 多条轨迹自行发明了 0.19.0、0.21.0 等移除版本。

因此，`resolved=true` 只说明通过了该次外部评测，不能证明完整兼容性或工程过程达到最佳水平。

## 三、可复用的 100 分轨迹评分细则

| 维度 | 权重 | 评分档位 | 主要扣分条件 | 可观察证据 |
|---|---:|---|---|---|
| A. 任务理解与成功标准 | 15 | 13–15：准确识别 API、兼容性和范围；8–12：抓住主问题但遗漏边界；0–7：误解目标 | 混淆维度与坐标；无依据扩大任务；未讨论弃用策略 | 推理文本、初始计划、明确列出的行为矩阵 |
| B. 定位与假设验证 | 15 | 13–15：比较相关实现、调用点、测试和惯例；8–12：只定位主函数；0–7：靠猜测修改 | 未查看 `Dataset`/`differentiate`；未搜索调用点；假定告警类别或版本 | `grep`/`rg`、源码阅读、测试与文档搜索 |
| C. 实现正确性与范围 | 25 | 22–25：契约正确、兼容、最小修改；15–21：核心正确但有边界或兼容缺口；0–14：明显行为错误 | 参数顺序破坏；静默忽略冲突；错误告警类别；无关文件/API 修改；格式缺陷 | 最终 `git diff`、签名、转发逻辑、异常与告警 |
| D. 验证质量 | 25 | 22–25：目标测试、回归测试、边界和静态检查齐全；14–21：部分真实测试；0–13：仅模拟、测试失败或未运行 | 只验证 happy path；没有运行修改后的项目；忽略失败；管道掩盖退出码 | pytest 数量与退出码、自建复现、compile/lint、失败分析 |
| E. 工具使用与错误恢复 | 10 | 9–10：错误定位准确且恢复干净；6–8：能恢复但绕行较多；0–5：重复试错或制造新风险 | 未引用 heredoc 导致命令替换；反复全量替换；`| head` 掩盖失败；随意安装不兼容依赖 | 命令序列、返回码、回滚和重新验证 |
| F. 收尾与证据完整性 | 10 | 9–10：清理临时文件、核对范围、检查补丁后提交；5–8：基本完成但证据不完整；0–4：错误或空补丁 | 未检查 diff/status；临时文件进入补丁；最终陈述超过证据 | `git status`、`git diff --name-only`、`cat patch.txt`、提交命令 |

设计上让实现与验证占 50 分，因为它们最直接决定可交付性；理解和定位占 30 分，用于区分偶然命中与证据驱动；恢复和收尾占 20 分，反映真实代理在不稳定环境中的可靠性。分数用于评估过程，不设为 `resolved` 的预测阈值。

## 四、逐轨迹诊断

分项顺序为 A/B/C/D/E/F。

| 轨迹文件 | `resolved` | 过程分 | 过程质量诊断 |
|---|---:|---:|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | true | 90（15/14/23/20/8/10） | 正确比较四个 API；处理 NumPy 2 不兼容后完成复现；自测覆盖位置参数、`coord`、`dim`、冲突、缺参和多坐标。未运行项目 pytest，且自行指定 0.21.0 移除版本。 |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | true | 86（15/14/18/22/7/10） | 自测全面且目标 pytest 为 2 passed；发现并删除编辑产生的重复 `return`。但签名变为 `(coord, dim, datetime_unit)`，破坏原第二位置参数 `datetime_unit` 的含义。 |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | true | 93（15/14/23/22/9/10） | 修改集中、保留第二位置参数、冲突与缺参处理明确；自测通过，相关 pytest 为 2 passed。另一次 DataArray 筛选没有收集到测试，模型正确识别了这一点。 |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | false | 66（13/12/13/14/5/9） | 找到问题并完成新旧参数自测，但无项目 pytest；多次修改和回滚，未引用 heredoc 触发命令替换。最终越界修改 `Dataset.integrate` 并使用 `DeprecationWarning`。过程存在可解释失败的实质风险。 |
| `20260217_mini-v2.0.0_glm-5-high.json` | true | 90（15/14/20/23/8/10） | 自测覆盖多坐标、datetime 和 API 一致性；相关测试为 4 passed、2 skipped。缺参仅由下层间接报错，局部重复导入 `warnings`，并写入无证据的 0.12.0 弃用起始版本。 |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | true | 87（15/14/22/18/8/10） | 参数顺序和范围正确；运行时验证新旧参数及告警，并执行 `compileall`。环境修复后没有运行 pytest，验证广度弱于高分轨迹。 |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | false | 54（13/12/12/3/5/9） | `git apply` 和首次替换失败后完成编辑，但唯一修改后运行因缺少 NumPy 失败，随后直接提交。最终逻辑未检查双参数冲突，使用无 `stacklevel` 的 `DeprecationWarning`。 |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | true | 79（15/14/18/16/6/10） | 自建测试覆盖主要行为；pytest 安装连续超时后改用运行时检查。签名中的 `*` 使原本可位置传入的 `datetime_unit` 变为仅限关键字，测试未覆盖这一兼容风险。 |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | true | 72（14/13/22/7/6/10） | 最终补丁本身集中且参数顺序合理，但环境始终未支持真实项目运行；只有 `py_compile` 和脱离 xarray 的模拟函数测试。移除版本 0.19.0 未经验证。 |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | false | 77（14/13/18/19/4/9） | 目标 pytest 为 2 passed，全文件测试为 814 passed、8 failed；失败项表面上与 integrate 无关，但未充分隔离。大量步骤用于修复 heredoc 造成的文档字符串损坏，最终仍遗漏方法间空行。 |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | true | 97（15/15/23/24/10/10） | 先运行基线目标测试；首次替换因反引号被 shell 展开后，立即检查 diff、回滚并改用引用 heredoc。修改后目标测试 4 passed，自测覆盖新旧参数、告警、冲突和缺参，并检查 status 与补丁范围。 |

## 五、关键案例

### 成功案例：`20260901_mini-v2.4.2_gemini-3-5-flash.json`

该轨迹最能说明错误恢复和验证闭环的价值：

- S18 在修改前运行 `test_trapz`，得到 4 passed，建立了环境基线。
- S25 的内联 Python 被 shell 展开反引号，输出出现 `dim: command not found`；S26 检查 diff 后发现告警文本被破坏。
- S27 主动恢复源文件；S28 的精确替换未命中后，S29 改用边界定位完成修改。
- S31 再次得到 4 passed；S32 验证位置参数、`coord=`、兼容 `dim=`、`FutureWarning`、双参数冲突和缺参。
- S33–S35 检查修改范围与补丁内容。

这里支持成功结论的不是“没有犯错”，而是错误被观测、回滚、换用更稳健手段，并以相同测试复验。其 `resolved=true` 与过程证据一致。

### 失败案例：`20260217_mini-v2.0.0_gpt-5-mini.json`

- S11 的补丁应用失败，S12 的文本替换也未命中，S14 才完成修改。
- S17 是唯一修改后运行，但在导入阶段因 `No module named 'numpy'` 失败；没有修复环境或重试。
- 最终代码只在 `coord is None` 时读取 `dim`。因此 `integrate(coord="x", dim="y")` 不报错、不告警，并静默使用 `"x"`。
- 告警为默认通常不可见的 `DeprecationWarning`，且没有 `stacklevel=2`。
- 最终回复声称已完成兼容，但缺少任何成功运行证据。

这条轨迹的 `resolved=false` 有较强的代码级解释，尤其是双参数冲突未处理和零有效回归测试；但没有外部评测日志，仍不能断言具体是哪一项触发失败。

### “测试通过仍失败”：`20260226_mini-v2.0.0_gemini-3-pro-high.json`

S52 的目标测试为 2 passed，核心改名、兼容和告警也能运行。然而最终 diff 中：

`return self._from_temp_dataset(ds)` 后立即出现 `def unify_chunks(...)`，缺少类方法间的空行。

这说明只运行功能测试无法覆盖格式、lint 或补丁质量要求。结合 `resolved=false`，静态质量缺陷是合理解释，但因没有 evaluator 输出，只能标为高可能性而非确定因果。

## 六、可执行改进建议

1. 修改前先写行为矩阵：位置 `coord`、`coord=`、旧 `dim=`、两者同时、缺参、多坐标、`datetime_unit` 的位置和关键字形式。
2. 保持最小范围：本任务只修改 `DataArray.integrate`；不要给已正确的 `Dataset.integrate` 增加反向别名。
3. 保留既有位置参数契约。推荐形态为 `coord, datetime_unit=None, *, dim=None`，避免把第二位置参数改成 `dim` 或把 `datetime_unit` 强制变成关键字。
4. 采用项目惯例的 `FutureWarning` 和 `stacklevel=2`；不要虚构弃用起始版本或移除版本。
5. 使用引用 heredoc（`<<'EOF'`）或结构化补丁工具，避免反引号触发 shell 命令替换。
6. 测试命令启用 `set -o pipefail`，不要让 `pytest ... | head` 把“pytest 未安装”包装成返回码 0。
7. 依照仓库 CI 约束安装兼容依赖；该旧版 xarray 与 NumPy 2.x 不兼容，应在首次安装前检查版本。
8. 至少运行：定向 pytest、独立 API 边界测试、`py_compile`，以及仓库已有的格式/lint 检查。
9. 对全套测试失败逐项判断是否与修改有关，不应仅凭“目标测试通过”收尾。
10. 提交前同时检查 `git diff --check`、`git diff --name-only`、完整补丁和临时文件状态。

## 七、证据局限与不能确定的结论

- 没有外部评测器的失败日志，不能确定三条失败轨迹究竟触发了哪一个隐藏测试或静态检查。
- 单个任务、每种设置仅一条样本，不能据此形成稳定的模型能力排名，也不能估计随机方差。
- 模型、日期、mini-swe-agent 版本和运行环境同时变化；尤其 2026-09-01 的成功不能单独归因于 Gemini 模型升级或代理框架 v2.4.2。
- 各轨迹通过 `pip install` 改变了环境，Python、NumPy、pandas 和 pytest 版本并不统一，测试结果不可直接横向等价比较。
- ATIF 是从 mini-swe-agent 格式转换而来，终止提交步骤通常没有 observation；只能以前一步的 `cat patch.txt` 作为提交内容证据。
- `resolved=true` 不证明完整向后兼容，`resolved=false` 也不证明核心思路完全错误。最明显的例子是成功的 Sonnet/Kimi 仍存在未覆盖的位置参数兼容风险，而失败的 Gemini Pro 已通过目标功能测试。
