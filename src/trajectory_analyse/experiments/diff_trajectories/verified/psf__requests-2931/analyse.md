# psf__requests-2931 轨迹评测报告

## 一、评测范围与结论

本报告逐条检查了目录中的 11 份 ATIF 轨迹，共 5 条 `resolved=true`、6 条 `resolved=false`。检查内容包括模型推理、命令与输出、最终补丁、测试、失败恢复和提交过程；`resolved` 仅作为外部结果单列，不直接决定过程得分。

任务的关键不只是“不要解码二进制 body”，而是 `_encode_params` 同时被两个上下文调用：

- 请求体：非 ASCII bytes 必须原样保留，否则 `to_native_string(..., encoding='ascii')` 会抛出 `UnicodeDecodeError`。
- URL 参数：`params=b'test=foo'` 必须转为 native string；直接保留 bytes 会造成 `urlunparse` 类型冲突或 `"b'test=foo'"` 污染 URL。这正是历史提交 `edc68a0` 为 issue #2844 增加的回归约束。

最强的总体发现是：**5 条成功轨迹全部保留或重新建立了 URL bytes 参数转换；6 条失败轨迹全部在共享 `_encode_params` 中无条件返回 bytes。** 这一分界与 `resolved` 完全一致。因而，成败主要由是否识别“共享 helper 的双重语义”决定，而不是模型运行步数、补丁大小或是否修复了题面示例。

成功轨迹采用了三类方案：

1. 在 `prepare_body` 调用点绕过 `_encode_params`，而让 URL 参数继续走原逻辑。这是语义最精确、作用域最小的方案。
2. 非 ASCII bytes 解码失败时回退为 bytes，同时让 ASCII bytes 继续转为字符串。
3. Python 3 下 `_encode_params` 返回 bytes，但在 `prepare_url` 中显式转换参数。

失败轨迹则只验证题面中的 body 分支，未验证历史回归 `test_params_bytes_are_encoded`。其中 GPT-5.2 和 Gemini 3 Pro 还把 Python 3.11 的 `collections` 兼容问题混入提交，进一步扩大了风险。

证据强度分三级：

- **强证据**：最终 diff、真实代码路径的 traceback、仓库已有定向测试输出。
- **中等证据**：在临时兼容 monkeypatch 或额外本地改动下运行的真实请求准备流程。
- **弱证据**：复制函数逻辑后的模拟脚本、捕获异常后仍返回 0、只打印“All tests passed”的脚本。

## 二、可复用的 100 分评分细则

| 维度 | 权重 | 高档 | 中档 | 低档及主要扣分条件 | 可观察证据 |
|---|---:|---|---|---|---|
| 任务理解与验收条件 | 15 | 13–15：同时定义目标、非目标和兼容要求 | 8–12：理解题面但遗漏隐含回归 | 0–7：把“示例不报错”等同于完整修复 | 早期推理、测试矩阵、最终总结 |
| 定位与系统上下文 | 15 | 13–15：追到调用点、共享 helper 和历史变更 | 8–12：定位正确但上下文不完整 | 0–7：只看报错行；未搜索调用者，扣 5–8 分 | 搜索、源码阅读、`git log/show` |
| 假设与回归分析 | 15 | 13–15：提出并验证 body/params 两条假设 | 8–12：有边界分析但覆盖不全 | 0–7：忽略已出现的反例或历史测试；每项扣 4–8 分 | 对照实验、失败后的推理更新 |
| 工具与执行纪律 | 10 | 9–10：命令精确、可复现、变更可控 | 6–8：有少量无效或绕行操作 | 0–5：大范围 `sed`、覆盖文件、修改环境来掩盖问题，扣 2–6 分 | 命令范围、退出码、工作区状态 |
| 实现正确性与作用域 | 20 | 17–20：行为完整、兼容、最小修改 | 10–16：目标成立但实现依赖内容启发式或有次要风险 | 0–9：留下已知回归、无关源文件进入补丁，分别扣 8–15、4–10 分 | 最终 patch、数据类型流、修改文件数 |
| 测试与验证证据 | 20 | 17–20：基线复现、目标测试、回归测试和合理套件 | 10–16：真实路径覆盖主要分支但环境受限 | 0–9：只做模拟、没有 params 测试、失败被捕获后仍宣称通过，分别扣 6–12、6–10、3–6 分 | traceback、pytest/unittest 输出、断言和退出码 |
| 错误恢复与收尾 | 5 | 5：根据反馈修正、清理临时改动、核对并按协议提交 | 3–4：最终正确但过程或协议有瑕疵 | 0–2：忽略失败、提交污染补丁、提交后继续操作 | `git status/diff`、清理、最终提交步骤 |

总分档位：90–100 为优秀，75–89 为较强，60–74 为基本合格但有风险，40–59 为薄弱，0–39 为无有效闭环。若补丁保留了轨迹中已经可观察到的关键回归，实现维度最高 8 分、总分最高 59 分。`resolved` 不自动加分或扣分。

这样设计是因为定位正确并不等于修复正确；实现和验证合计 40 分，确保真实行为证据占主导。历史兼容约束、共享调用点和失败恢复各自计分，可区分“偶然命中”“正确但证据不足”和“有完整工程闭环”。

## 三、关键案例

### 成功案例：Gemini 3.5 Flash，过程分 97，`resolved=true`

文件：`20260901_mini-v2.4.2_gemini-3-5-flash.json`

- step 11–12 查看 `edc68a0`，明确 `_encode_params` 的修改是为 `params=b'test=foo'` 服务，而不是专门处理 body。
- step 14 在真实 `Request.prepare()` 路径复现 `UnicodeDecodeError`。
- step 41 没有破坏共享 helper，而是在 `prepare_body` 中对 `basestring` 直接赋给 `body`，仅非字符串表单数据继续调用 `_encode_params`。
- step 43 验证 UTF-8 bytes body 类型和值都保持为 bytes。
- step 45 的 47 个 unittest 全部通过；step 60 又直接运行仓库已有的 `test_params_bytes_are_encoded`，1/1 通过。
- 完整 pytest 在 step 44 因递归 `httpbin` fixture 出错，但模型没有把它误报为补丁通过，并补充了可运行的测试集合。
- 最终 diff 只有 `requests/models.py` 一处三行逻辑变化。

这是最强轨迹，因为修改位置表达了真实语义：问题在 body 调用方，不在通用参数编码器。

### 失败案例：Claude 4.6 Opus，过程分 52，`resolved=false`

文件：`20260217_mini-v2.0.0_claude-4-6-opus.json`

- step 5 正确定位 `to_native_string` 的 ASCII 解码问题。
- step 8 将共享逻辑直接改为 `if isinstance(data, (str, bytes)): return data`。
- step 9 真实导入被 Python 3.11 的 `collections.MutableMapping` 阻断；step 11 随后只测试了复制出来的简化函数。
- 轨迹没有查看引入变更的 `edc68a0`，也没有测试 `params=b'test=foo'`。
- 最终断言“对 str 和 bytes 都正确”缺少调用上下文证据。

该补丁确实修复题面 body，但会让 URL 参数保持为 bytes。其他轨迹已直接证明这种状态会导致 `urlunparse` 的 “Cannot mix str and non-str arguments”。因此失败不是定位错误，而是验收条件过窄。

### 错误恢复案例：GLM-5，过程分 86，`resolved=true`

文件：`20260217_mini-v2.0.0_glm-5-high.json`

GLM-5 最初也让 bytes 原样返回，但 step 41/43 实际观察到 bytes 参数会污染格式或触发 `urlunparse` 类型错误。此后改为“ASCII bytes 解码为字符串，非 ASCII bytes 保留”，step 59 同时验证 URL 参数与二进制 body，step 60 真实 HTTP 请求返回 200。虽然过程中修改了多个兼容文件、出现语法错误和带失败项仍打印成功的测试脚本，但最终只提交 `requests/models.py`。它说明早期假设错误并非决定性，是否根据反例更新实现才是关键。

## 四、逐轨迹诊断

| 轨迹文件 | 过程分 | `resolved` | 过程质量与结果诊断 |
|---|---:|:---:|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | 91 | true | 查阅历史提交并识别 ASCII params 约束；用异常回退保留两条路径。真实导入受环境阻断，主要证据来自模拟流程，弱于官方测试。 |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | 92 | true | 同时验证非 ASCII body、ASCII params 和任意 bytes；最终单文件补丁。测试依赖 `collections` monkeypatch，且曾出现捕获断言后仍打印总通过，但随后修正。 |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | 52 | false | 快速、最小，但只覆盖题面；无条件返回 bytes 破坏 URL 参数。真实测试失败后退化为复制逻辑模拟。 |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | 87 | true | 实际发现 params 回归，并在 `prepare_url` 补回 native string 转换；覆盖 body、字符串、字典和参数。过程有错误 `sed`、缩进错误、广泛临时修改和提前提交后继续操作。 |
| `20260217_mini-v2.0.0_glm-5-high.json` | 86 | true | 能从失败的 bytes params 测试修正初始方案，最终补丁只保留目标文件。过程冗长，曾修改环境、手工拼 patch，且若干脚本把失败吞掉。 |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | 49 | false | 验证了 bytes body、文本和 bytearray，但未验证 params；最终提交 6 个源文件的 Python 3.11 兼容改动，并扩大 bytearray 行为，作用域明显失控。 |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | 46 | false | 最终补丁只改目标文件，但两次错误使用 `git apply`；唯一真实请求准备测试被环境导入错误阻断，未建立替代的真实路径或参数回归测试。 |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | 52 | false | 正确解释 body 根因并保持补丁最小，但测试的是复制函数；虽然阅读了调用点，却错误地认为 `basestring` 检查足以保证兼容，完全漏测 URL 参数。 |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | 47 | false | 花费大量步骤修改 Python 3.11 兼容问题，最后能清理无关文件；但验证仍是简化模拟，并将共享 helper 全部改为原样返回。 |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | 44 | false | 真实验证了目标 body 和普通字符串，但无 params 测试；覆盖写 `compat.py`、批量 `sed` 并提交 7 个文件，期间还产生错误的 `isinstance(name, basestring, Mapping)`，虽修复但显示变更控制较弱。 |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | 97 | true | 调用点级修复、基线复现、官方回归测试和单文件最终 diff 均完整；完整 pytest 的 fixture 环境错误被正确区分，是证据最强且语义最清晰的轨迹。 |

## 五、可执行改进建议

1. 修改前建立最小行为矩阵：非 ASCII bytes body、ASCII bytes body、`params=b'test=foo'`、文本、字典、文件对象、空值，并明确期望类型和内容。
2. 对共享 helper 必须搜索全部调用点。这里优先在 `prepare_body` 区分原始 body 与表单编码，而不是按“字节内容是否可 ASCII 解码”猜测用途。
3. 使用 `Request(...).prepare()` 做离线真实复现，避免把网络连通性混入核心验证；真实 HTTP 只作为补充。
4. 至少运行题面回归和历史回归两个定向测试。任何脚本都应让断言失败产生非零退出码，不能捕获后继续打印“All tests passed”。
5. 旧项目与现代 Python 不兼容时，应使用匹配的解释器或测试侧 monkeypatch；不要把运行环境兼容改动混入任务补丁。
6. 实验性修改结束后执行 `git status`、限定文件的 `git diff` 和语法检查，确认提交只含目标源文件；提交命令执行后不再继续操作。
7. 自动评测可增加两个强制门禁：检查 `params=b'test=foo'` 的 URL，以及检查 prepared body 与输入 bytes 的值和类型完全一致。

## 六、不能由这些轨迹单独确定的事项

- 缺少外部 grader 的测试名和失败日志，因此可以高可信地指出共同回归，但不能证明它是 GPT-5.2、Gemini 3 Pro 等失败的唯一原因；无关兼容补丁也可能造成额外失败。
- 单一仓库、单一 issue、每个设置一次采样，不能据此形成通用模型能力排名，也不能分离随机采样、提示模板和工具策略的影响。
- `mini-v2.4.2` 轨迹使用 Python 3.9，而多数早期轨迹面对 Python 3.11；日期、agent 版本和环境均不完全一致，不是严格控制变量实验。
- Python 2 在多数轨迹中不可用，相关兼容性主要来自源码推理，缺少运行证据。
- 网络测试成功不能证明跨代理、流式上传、大文件、重试和所有传输适配器均正确；性能、安全性及更广泛 API 兼容性也未被这些轨迹覆盖。
