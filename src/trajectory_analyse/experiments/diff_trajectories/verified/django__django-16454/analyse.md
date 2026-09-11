# Django #16454 轨迹评测报告

## 总体结论

共审阅 11 条完整 ATIF 记录，外部 `resolved=true` 为 3/11（27.3%）。成功与失败的主要分界不在于是否意识到“子解析器没有继承 `called_from_command_line`”，多数轨迹都定位到了这一点；分界在于是否同时保持 `argparse` 的扩展契约：

- 成功实现会只对 `CommandParser` 或其子类传递 Django 专用参数，并保留调用方的 `parser_class`、`action` 和显式 `False`。
- 失败实现常把 `called_from_command_line`、`missing_args_message` 无条件塞给任意子解析器。对普通 `argparse.ArgumentParser` 或不接受这些参数的自定义 parser，会触发 `TypeError` 或改变原有行为。
- 可见回归通过并不足够。多条失败轨迹通过了 `user_commands`、甚至 `admin_scripts`，但没有覆盖自定义 `parser_class`、显式 `False`、调用方传入自定义 `action` 等兼容性边界。
- 成功轨迹普遍有“观察失败行为 -> 修改 -> 同时验证命令行与程序化调用”的闭环。最强的一条还做了基线、模块回归、相邻模块回归和 `git diff --check`。
- 过程证据强度不等同于最终结果：`resolved` 是外部评测，隐藏测试内容不可见。因此对失败根因中“哪条隐藏断言失败”的判断为代码级推断；“实现确实缺少兼容性保护”则有补丁直接证据。

不能据此归因于模型能力本身：每个模型设置只有一个样本，且日期、依赖安装状态、代理版本不同，最新成功样本还使用 `mini-v2.4.2`，存在明显混杂因素。

## 可复用评分细则（100 分）

| 维度 | 权重 | 优秀 | 合格 | 不足/失效 | 可观察证据与扣分 |
|---|---:|---|---|---|---|
| 任务理解与复现 | 20 | 18-20：准确区分 CLI 与 API 调用并复现目标错误 | 12-17：复现主路径 | 0-11：只凭描述猜测或复现失败后未恢复 | 查看复现脚本、异常类型、stderr。未直接复现扣 5-10。 |
| 定位与假设 | 15 | 13-15：阅读目标代码、调用链及 stdlib 行为 | 8-12：找到目标类但未验证机制 | 0-7：盲改 | 查看 `CommandParser`、`add_subparsers()`、`_SubParsersAction.add_parser()` 的读取与推理。 |
| 设计正确性与兼容性 | 25 | 22-25：保留 `parser_class`/`action`/显式值，处理嵌套与非 Django parser | 14-21：主路径正确但边界不全 | 0-13：覆写 stdlib 行为或无条件注入私有参数 | 无 `issubclass(..., CommandParser)` 防护扣 8-15；把 `False` 当未设置扣 3-5；重写整段 stdlib 私有流程扣 4-8。 |
| 验证覆盖与反馈解读 | 20 | 18-20：基线、目标行为双模式、相关回归、失败均有诊断 | 11-17：目标测试和部分回归 | 0-10：只编译、只看单次输出或忽略失败 | 管道掩盖退出码扣 2-4；测试失败未修复仍收尾扣 4-8。 |
| 工具选择与错误恢复 | 10 | 9-10：命令失败后快速替代并确认状态 | 5-8：可恢复但反复试错 | 0-4：错误命令后直接假定成功 | 如依赖缺失、补丁失败、测试路径错误后的处理。 |
| 交付卫生 | 10 | 9-10：仅源文件、清理临时文件、检查 diff/状态后提交 | 5-8：可提交但有杂项或未检查 | 0-4：暂存导致错误 diff、无可验证补丁 | 未做 diff 检查扣 2-3；提交空/错误 patch 或保留临时物扣 4-8。 |

这样分配权重是因为该缺陷的难点不是找到文件，而是维护 `argparse` 可扩展接口；设计兼容性与可证伪的验证应高于命令数量和叙述长度。

评分档位：90-100 为证据充分且工程上可靠；75-89 为主路径可靠但有覆盖缺口；60-74 为局部正确、隐藏风险高；40-59 为关键闭环缺失；低于 40 为交付或验证失效。

## 具体案例

### 成功：`20260901_mini-v2.4.2_gemini-3-5-flash.json`

该轨迹过程质量最高（91/100），且 `resolved=true`。步骤 3-10 同时检查 `CommandParser` 和 `argparse` 的 `add_subparsers()`、`_SubParsersAction.add_parser()` 机制；步骤 18 先获得 `user_commands` 44 项基线通过。步骤 19 明确观察到子 parser 的两个 Django 属性均为 `None`，并确认错误变成 `CommandError`。修改后，步骤 26 验证 CLI 得到 `SystemExit` 与 usage，而 `called_from_command_line=False` 仍得到 `CommandError`。

其补丁中的关键保护是仅在 `issubclass(self._parser_class, CommandParser)` 时转发 Django 专用参数。步骤 27、35 运行 `user_commands`，步骤 36 运行 `admin_scripts` 210 项，步骤 37 执行 `git diff --check`。这形成了定位、行为验证、回归和交付检查的完整证据链。

### 失败：`20260217_mini-v2.0.0_claude-4-6-opus.json`

该轨迹过程并不差（74/100），但 `resolved=false`。步骤 9 已正确观察到父 parser 的 `called_from_command_line=True` 而子 parser 为 `None`；步骤 13 修复后也验证了 CLI 的 `SystemExit` 和程序化调用的 `CommandError`。步骤 20、21 的 `user_commands` 与 `admin_scripts` 可见回归均通过。

但最终补丁把返回 action 的 `add_parser` 包装为闭包，只要父 parser 的值非 `None` 就传入 `called_from_command_line`，没有检查实际 `parser_class` 是否为 `CommandParser` 子类。该代码对普通 `argparse.ArgumentParser` 的自定义 `parser_class` 不兼容。这是补丁可直接观察到的缺陷；外部失败很可能由此类未覆盖边界触发，但无法仅凭轨迹确定具体隐藏测试名称。

MiniMax 成功轨迹提供了反证：`20260217_mini-v2.0.0_minimax-2-5-high.json` 在步骤 68 实际遇到普通 `ArgumentParser` 收到 `called_from_command_line` 后的 `TypeError`，随后加入 parser 类型判断，并在步骤 72 验证自定义 parser 路径。这是“兼容性保护是关键差异”的最强过程证据。

## 逐条诊断

| 轨迹 | resolved | 过程质量分 | 过程诊断 |
|---|---:|---:|---|
| `claude-4-5-opus-high` | 否 | 72 | 有源码、stdlib 与目标回归测试；补丁仅传播 `called_from_command_line`，且未按 `parser_class` 类型保护，遗漏兼容性边界。 |
| `claude-4-5-sonnet-high` | 否 | 65 | 有较完整复现和回归，但曾因手工重写 `add_subparsers()` 触发缺少 `option_strings`；最终仍无 parser 类型保护，且不必要复制私有 stdlib 逻辑。 |
| `claude-4-6-opus` | 否 | 74 | 主路径复现、双模式验证和两个套件均通过；闭包式修复无 `parser_class` 保护，属于“可见测试绿、接口边界失败”。 |
| `gemini-3-flash-high` | 否 | 69 | 复现后修复了初版 malformed patch，也验证 CLI/API；自定义 action 仍未按实际 parser 类型限制参数注入，且测试集中在主路径。 |
| `glm-5-high` | 否 | 72 | 试错较多，但步骤 68 曾直接发现普通 parser 的 `TypeError`，后续回归较广；最终提交证据未充分证明已保持所有调用方参数语义，过程冗长且部分命令失败被弱化。 |
| `gpt-5-2-high` | 是 | 82 | 阅读 argparse 内部实现后采用受保护的自定义 action；直接验证 CLI/API 两种语义、编译和 diff。未运行 Django 套件，依赖通过 pip 临时安装，验证广度不足但外部结果成功。 |
| `gpt-5-mini` | 否 | 36 | 初始 patch 与脚本均失败；虽做了 stub 验证，但未完成真实测试。`git add -A` 后 `git diff` 为空，交付流程混乱；补丁还向任意 parser 注入 formatter 与 Django 私有参数。 |
| `kimi-k2-5-high` | 否 | 67 | 能复现 CLI 问题并运行部分回归；最终 custom action 无条件传递 Django 参数，且显式参数会与调用方 kwargs 冲突。测试未有效覆盖普通 `ArgumentParser`。 |
| `minimax-2-5-high` | 是 | 79 | 过程极其曲折，但实际触及自定义 parser `TypeError` 后修正；验证 CLI、API、嵌套、自定义 parser 及 44 项回归。主要扣分为大量无效临时项目和多次错误测试路径。 |
| `gemini-3-pro-high` | 否 | 68 | 主复现、CLI/API、递归子解析器均通过；最终仅在 truthy 时传播 `called_from_command_line`，不能保留显式 `False`，且无项目回归与普通 parser 测试。 |
| `gemini-3-5-flash` | 是 | 91 | 基线、根因验证、CLI/API 行为、44 项与 210 项回归、diff 检查均具备；兼容性设计明确。 |

## 可执行改进建议

1. 固化最小测试矩阵：默认 `CommandParser` 的 CLI/API、显式 `False`、嵌套子解析器、`parser_class=argparse.ArgumentParser`、`parser_class=CommandParser` 子类、调用方自定义 `action`。
2. 在修改前读取并执行 stdlib `ArgumentParser.add_subparsers()` 与 `_SubParsersAction.add_parser()` 的实现；避免复制整段私有实现，优先局部 action 子类或小型包装。
3. 传播参数时以“目标 parser 是否接受 Django 扩展参数”为前提，而不是以父对象值是否非空为前提；保留 `setdefault()` 和显式 `False`。
4. 任何测试命令经 `tail`、`grep` 管道时启用 `pipefail` 或单独检查测试进程退出码，避免“命令返回 0、测试实际失败”的假阳性。
5. 把依赖恢复、临时复现文件清理、`git diff --check`、仅目标源文件 diff 作为固定收尾门槛；禁止在提交前无必要 `git add -A`。

仅凭这些轨迹不能确定外部评测的确切断言、依赖安装是否影响判分，也不能推出模型之间存在稳定能力排序。能确定的是：成功补丁与失败补丁在 `parser_class` 兼容性和显式状态保持上存在系统性代码差异，而可见测试覆盖常不足以暴露该差异。
