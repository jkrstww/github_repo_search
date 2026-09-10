# Django `LEVEL_TAGS` 问题轨迹评测报告

## 评测范围

共分析 11 个 ATIF JSON 轨迹，按给定 `compare.json` 映射计：

- `resolved=true`：7 条
- `resolved=false`：4 条

`resolved` 仅作为外部最终结果；下文“过程分”根据轨迹中可观察的理解、命令、补丁和验证证据独立评定，不直接由 `resolved` 决定。

## 总体结论

问题根因是 `django.contrib.messages.storage.base.LEVEL_TAGS` 在模块导入时由 `utils.get_level_tags()` 计算一次。`override_settings(MESSAGE_TAGS=...)` 会发送 `setting_changed`，但原实现没有据此刷新该模块变量。

样本呈现出非常强的补丁形态分界：

| 最终方案 | 样本结果 | 诊断 |
|---|---:|---|
| 监听 `setting_changed`，在 `MESSAGE_TAGS` 改变时更新 `LEVEL_TAGS` | 7/7 `true` | 同时修复模块状态和 `Message.level_tag`，覆盖 override 进入与退出 |
| 仅将 `Message.level_tag` 改为动态调用 `utils.get_level_tags()` | 0/4 `true` | 修复消费者的表面行为，但 `LEVEL_TAGS` 本身仍然陈旧 |

因此，失败的核心不是“不会动态读取设置”，而是把验收契约缩窄成了 `Message.level_tag` 的返回值。现有测试中的 `override_settings_tags` 明确写着：修改 `settings.MESSAGE_TAGS` 后还要更新 `base.LEVEL_TAGS`。Sonnet、Kimi、Gemini Pro 都读到了这类证据，却仍选择绕开常量。

测试数量也不是决定因素。Sonnet 和 Gemini Pro 的一行动态查询补丁均跑过 96 个消息测试，但仍为 `resolved=false`；原因是现有套件使用自定义 workaround，本身不能区分“常量已更新”和“消费者绕过常量”。相反，GPT-5.2 没有跑完整套件，最终仍因补丁契约正确而通过外部评测。

轨迹长度同样没有单调关系：21 步的 Claude 4.6 Opus 成功，43 步的 Sonnet 失败；71 步的 MiniMax 最终成功，但过程包含多次破坏性重写和语法错误。本文过程分均值约为成功组 89、失败组 64，仅是该小样本的描述性结果，不能视为模型能力统计。

## 可复用的 100 分评分细则

| 维度 | 权重 | 高档 | 中档 | 低档及典型扣分 | 可观察证据 |
|---|---:|---|---|---|---|
| 任务理解与契约识别 | 15 | 13–15：区分模块状态、消费者行为、override 进入/退出 | 8–12：理解缓存问题但只关注表面症状 | 0–7：未形成根因；把 `level_tag` 正确等同于 `LEVEL_TAGS` 已更新，扣 4–7 | reasoning、复现断言、对问题标题和描述的解释 |
| 定位与假设质量 | 15 | 13–15：检查 `base.py`、`utils.py`、现有测试 workaround 和类似 signal 实现 | 8–12：定位正确但证据链不全 | 0–7：直接猜改；未查引用或相邻模式，各扣 2–4 | 搜索命令、读取文件、类比 `auth/hashers.py` 等 |
| 实现正确性与范围 | 25 | 22–25：响应 `MESSAGE_TAGS`、进入和恢复均刷新，改动最小 | 14–21：主症状可用但有兼容缺口或无关改动 | 0–13：只修消费者、最终代码损坏或改错文件；动态绕过常量通常不高于 13 | 最终 patch、状态管理方式、无关 diff |
| 验证强度 | 25 | 22–25：先失败后通过；检查 `LEVEL_TAGS`、`level_tag`、恢复/嵌套；再跑回归套件 | 14–21：有定向测试和回归，但漏掉关键契约 | 0–13：没有有效测试、只编译、跑 0 tests；未断言模块变量扣 6–10 | 测试命令、真实返回码、断言内容、测试数量 |
| 工具纪律与错误恢复 | 10 | 9–10：识别环境错误并准确修复，编辑和命令范围受控 | 5–8：最终恢复，但有重复误调用或整文件重写 | 0–4：忽略错误、管道掩盖失败、`git add -A` 或宽泛删除带来风险 | command/observation、失败后的下一步 |
| 收尾与可复现性 | 10 | 9–10：清理临时文件，核对 status/diff，补丁仅含目标源码 | 5–8：补丁正确但检查不完整或含无关格式改动 | 0–4：空补丁、残留测试修改、未核对最终内容 | `git status`、最终 patch、提交命令 |

总体档位：A 为 90–100，B 为 75–89，C 为 60–74，D 为 40–59，F 为 0–39。缺失证据不按“可能做过”补分；被 `head`/`tail` 管道掩盖的退出码只按输出内容计分。

实现和验证合计占 50 分，因为代码是否满足完整契约、证据能否排除代理性修复，最直接决定外部结果；理解与定位占 30 分，用于衡量形成正确补丁的可迁移能力；错误恢复和收尾占 20 分，反映真实工程执行的稳定性。

## 逐条轨迹诊断

| 轨迹文件 | `resolved` | 过程分 | 过程质量诊断 |
|---|---:|---:|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | true | 96 | 完整读取 workaround、utils 和 signal 先例；先复现失败，再验证嵌套、恢复和空标签，最终 96 tests 通过，补丁聚焦于 signal receiver。 |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | false | 79 | 定位、复现、错误恢复和 96 tests 都较扎实；但尽管读到 `override_settings_tags` 会更新 `base.LEVEL_TAGS`，仍只动态改 `level_tag`，验证也只覆盖消费者行为。 |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | true | 92 | 识别 contrib app 的 signal 模式，短暂评估 `AppConfig.ready()` 后采用既有 decorator 风格；定向脚本和 96 tests 通过，最终 diff 简洁。 |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | true | 89 | 经两次复现脚本配置错误后恢复；曾尝试动态查询，随后意识到需要原位更新字典，最终定向复现和 96 tests 均通过。 |
| `20260217_mini-v2.0.0_glm-5-high.json` | true | 83 | 正确采用 `setting_changed.connect()`，有充分的恢复、嵌套和完整回归测试；整文件重写意外删除文档字符串中的一个词，降低改动纯度。 |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | true | 82 | 先尝试缓存函数方案，随后回退 `utils.py` 并改为正确的 signal receiver；定向脚本和编译通过，但没有执行正式消息测试套件。 |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | false | 42 | 仅粗略检查两个源码文件便决定动态查询；唯一运行时验证因缺少 `asgiref` 失败且未恢复，还执行了 `git add -A`，最终只能证明补丁文本存在。 |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | false | 66 | 有前后复现和多个边界测试，但正式测试多次因路径/配置错误失败；与 Sonnet 相同，只验证 `Message.level_tag`，未验证 `LEVEL_TAGS`。 |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | true | 81 | 最终 signal 补丁和 96 tests 正确；过程先后产生错误 decorator、删除常量、语法错误和无关文档修改，最后通过恢复原文件再重做才收敛。 |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | false | 70 | 能稳定复现并跑通 96 tests 与编译检查，但没有调查 `setting_changed` 方案，最终仍是一行动态查询，完整测试提供了错误的完成感。 |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | true | 98 | 先跑 96-test 基线，再加入定向测试并观察第 97 个测试失败；原位刷新 `LEVEL_TAGS` 后 97 tests 通过，恢复测试文件，再跑 96 tests 并核对仅源码有 diff。临时改 tracked test 违反任务边界，故非满分。 |

## 关键案例

### 成功案例：Gemini 3.5 Flash

`20260901_mini-v2.4.2_gemini-3-5-flash.json` 提供了最强的因果证据：

1. Step 11：原始 96 个消息测试全部通过，说明基线套件不会暴露缺陷。
2. Step 16–17：加入标准 `@override_settings` 回归测试后，第 97 个测试以 `'info' != 'custom-info'` 失败。
3. Step 21：注册 `setting_changed` receiver，并以 `clear()`/`update()` 原位刷新 `LEVEL_TAGS`。
4. Step 23：包含新回归测试的 97 tests 全部通过。
5. Step 27–31：恢复测试文件，再跑原有 96 tests，最终 patch 只包含 `base.py`。

这条轨迹同时证明了缺陷、补丁效果、回归安全和最终提交范围。原位更新还保留了已导入字典对象的身份，比简单重绑定具有更强的引用兼容性；不过外部结果显示重绑定方案同样可被当前评测接受。

### 失败案例：Claude 4.5 Sonnet

`20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` 的失败并非缺少努力：

- Step 9 已读到 `override_settings_tags` 明确保存并重设 `base.LEVEL_TAGS`。
- Step 16 却把方案收缩为 `return utils.get_level_tags().get(...)`。
- Step 18–19 的自建测试只检查 `Message.level_tag`，因此全部通过。
- Step 25 和 34 的 96 tests 也全部通过。
- 最终 patch 仅有这一行动态查询，外部结果为 `false`。

这说明“测试通过”只有在测试断言覆盖真实契约时才有意义。该方案绕过陈旧变量，并未使变量得到更新；测试设计与实现共享了同一个错误假设。

### 过程与结果不等价：MiniMax

`20260217_mini-v2.0.0_minimax-2-5-high.json` 最终为 `true`，但过程明显弱于 Sonnet：曾使用不可调用的 `@setting_changed`、删除 `LEVEL_TAGS` 导致已有测试报错、整文件重写造成语法错误，并多次恢复重做。它最终通过重新还原源码、加入 signal handler 和执行 96 tests 获得正确补丁。这表明 `resolved=true` 不能抹去高成本、低稳定性的执行过程。

## 可执行改进建议

1. 把验收条件写成三层断言：override 内 `base.LEVEL_TAGS` 已更新、`Message.level_tag` 使用新值、退出或嵌套恢复后两者回到正确状态。
2. 将现有 workaround 当作架构证据，而非仅作测试背景；看到测试手工刷新模块状态时，应优先补齐框架的失效通知机制。
3. 首选最小 signal 补丁：仅响应 `MESSAGE_TAGS`。是否重绑定或原位更新应由对象身份兼容性测试明确决定，而不是偶然选择。
4. 定向测试必须先在原实现上失败，再在补丁后通过；随后运行 `PYTHONPATH=. python tests/runtests.py messages_tests`，不要通过 `head`/`tail` 隐藏真实退出码，必要时启用 `pipefail`。
5. 避免整文件覆盖、`git add -A` 和宽泛的 `rm test_*.py`；使用局部编辑，并在结束时运行 `git diff --check`、`git status --short` 和目标文件 diff。
6. 环境依赖错误应与代码失败分开记录。先按测试 README 修复 `asgiref`、`sqlparse` 或 `PYTHONPATH`，不能把“命令返回 0 但输出包含 FAILED”当作有效通过。

## 证据局限与不可确定事项

- 外部结果只有布尔值，没有 grader 测试名称或失败栈；“动态查询因未更新 `LEVEL_TAGS` 而失败”是由问题契约、现有 workaround 和 7/7 对 0/4 的补丁分界共同支持的强推断，不是直接看到的隐藏测试结果。
- 部分模型的内部 reasoning 未记录，且若干命令主动截断输出，因此只能评价可观察行为。
- 每个模型设置只有一次运行，不能区分模型能力、随机性、日期、agent 版本或环境预装依赖的影响。
- 只有一个 mini-v2.4.2 样本，不能据此断言 v2.4.2 优于 v2.0.0。
- 重绑定和原位更新在本样本中都能 `resolved=true`，所以不能确定隐藏评测是否要求保持通过 `from ... import LEVEL_TAGS` 获得的旧对象引用。
- 这些轨迹不能证明补丁在并发设置修改、第三方扩展或完整 Django 全套测试中的所有兼容性；大多数轨迹只运行了 `messages_tests`。
