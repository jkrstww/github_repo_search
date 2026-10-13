# Django #11532 轨迹评测报告

## 1. 总体结论

本目录包含 11 条 ATIF 轨迹：

- `resolved=true`：8 条
- `resolved=false`：3 条
- 步数范围：17 至 75
- 任务核心：主机名 `DNS_NAME` 含非 ASCII 字符时，`EmailMessage.message()` 在 `iso-8859-1` 等非 Unicode 编码下生成 `Message-ID` 会触发 `UnicodeEncodeError`；正确行为应将域名转换为 IDNA/Punycode，例如 `漢字 -> xn--p8s937b`。

成功轨迹通常具备以下共同特征：

1. 能定位完整调用链：`django/core/mail/message.py` 的 `make_msgid(domain=DNS_NAME)`、`utils.py` 的 `CachedDnsName`，并注意到 SMTP 后端也使用 `DNS_NAME`。
2. 先复现原始错误，再修改代码。
3. 能处理 `DNS_NAME` 的类型差异：实际对象是 `CachedDnsName`，而回归测试可能把 `message.py` 中的别名直接替换为字符串。
4. 修改后同时验证：
   - 非 ASCII 域名；
   - ASCII 域名不受影响；
   - 混合域名、UTF-8 编码等边界；
   - Django mail 测试套件。
5. 最后检查 diff、工作区状态和提交补丁内容。

失败或存在较大风险的轨迹主要有两类：

- **验证场景被改变**：只测试 `socket.getfqdn()` 或 `CachedDnsName`，没有再测试 PR 中直接替换 `django.core.mail.message.DNS_NAME` 的场景。这样可能使一个“看似通过”的修复仍然无法处理测试实际注入的字符串。
- **修复范围或收尾不足**：只修改 `utils.py`、未重新验证 `message.py` 中已经导入的别名，或没有完成最终测试套件。

`resolved` 与过程质量并不完全一致。例如 Claude Sonnet 轨迹在日志中显示精确场景和 157 个 mail 测试均通过，但外部标签仍为 `false`。这说明 `resolved` 可能受隐藏测试、补丁提交方式或未覆盖的契约影响，不能简单等同于“最后一条命令是否显示 OK”。

### 证据强弱

证据强度由高到低大致为：

1. 修改后、使用与 PR 完全相同注入方式的回归测试通过；
2. 修改后运行完整 mail 测试套件并显示 `OK`；
3. 对 ASCII、混合域名、缓存行为等边界的独立测试；
4. 只检查 diff 或打印 Punycode；
5. 只执行命令但输出被截断、通过 `head/tail` 管道掩盖退出码，或测试场景与原问题不一致。

## 2. 可复用的 100 分轨迹评测细则

该评分评估“过程质量和可验证的技术正确性”，不直接把外部 `resolved` 标签当作分数。这样可以区分“过程扎实但隐藏测试失败”和“标签成功但证据不足”。

| 维度 | 权重 | 高档 | 中档 | 低档 | 扣分条件与可观察证据 |
|---|---:|---|---|---|---|
| 任务理解与验收标准 | 15 | 明确指出非 ASCII `DNS_NAME`、邮件编码、`Message-ID` 和 IDNA 目标 | 能理解错误但验收条件不完整 | 仅笼统描述“编码问题”或误解为地址本地部分问题 | 未说明 `xn--...` 或未区分域名与邮箱用户名，扣 3-6 分 |
| 定位与假设 | 15 | 追踪 `message.py`、`utils.py`、必要时 SMTP 后端，并验证导入别名/对象类型 | 找到主要代码点，但未检查相关消费者 | 盲改或只搜索文件名 | 未验证 `DNS_NAME` 实际类型、patch 位置或调用链，扣 3-5 分 |
| 工具与命令策略 | 10 | 搜索、局部阅读、最小复现、增量验证顺序清楚 | 有若干环境或命令问题，但能恢复 | 大量无效/畸形命令，缺少观察反馈 | 每个可避免的命令失败扣 1 分，最多扣 4 分；依赖问题未诊断扣 2 分 |
| 修改正确性与范围 | 20 | 在正确边界做 IDNA 转换，兼容对象和字符串，修改仅限必要源码 | 主路径可用，但存在消费者遗漏或类型风险 | 修复未生效、只改测试、或改动范围失控 | 只通过改变后的测试而未覆盖原注入方式，扣 5-7 分； broad `except` 或无依据 fallback，扣 2-4 分 |
| 测试设计与反馈 | 15 | 有原始回归测试、ASCII/Unicode 边界和完整套件，且确认是修改后的结果 | 有回归测试或完整套件之一 | 只打印结果、未运行测试或测试仍失败 | 未执行精确回归测试扣 4 分；测试不可用却宣称通过扣 3-5 分 |
| 错误恢复 | 10 | 能识别依赖缺失、类型错误、错误断言，并修正后重测 | 能恢复主要错误，但有未闭环问题 | 忽略异常或在失败状态收尾 | 未处理关键失败扣 2-5 分；反复错误命令且无总结扣 1-2 分 |
| 收尾与验证证据 | 15 | 检查最终 diff/status，确认只含目标源码，给出可靠测试输出 | 有 diff 或测试，但证据不完整 | 无最终 diff、残留临时文件或提交前状态不明 | 管道掩盖退出码、测试文件未恢复、最终补丁未核对，扣 3-5 分 |

设计理由：

- 任务理解、定位和修改正确性合计 50 分，覆盖“是否找对问题及修对代码”。
- 测试和收尾合计 30 分，防止仅凭局部打印或自述判定成功。
- 工具策略和错误恢复合计 20 分，用于区分稳健的工程流程与偶然成功。
- 不把 `resolved` 直接计入分数，可避免外部标签反向污染对轨迹证据的判断。

## 3. 具体案例

### 案例 A：成功轨迹

文件：`20260217_mini-v2.0.0_minimax-2-5-high.json`

关键事件：

- 第 8 步直接构造 `EmailMessage`，在 `DNS_NAME="漢字"`、`encoding="iso-8859-1"` 下复现 `UnicodeEncodeError`。
- 第 10 步先修改 `CachedDnsName.get_fqdn()`，但第 11 步发现测试直接 patch `django.core.mail.message.DNS_NAME` 为字符串后仍然失败。
- 第 13 步改用新的 `CachedDnsName` 并 patch `socket.getfqdn()`，验证缓存对象路径。
- 第 22-23 步验证 `漢字 -> xn--p8s937b`，并确认 `Message-ID` 正常生成。
- 第 28 步运行完整 mail 测试：`Ran 162 tests ... OK`。
- 第 31 步再次验证 PR 场景，输出 `Message-ID: ...@xn--p8s937b`。
- 第 35-36 步补充 ASCII、混合域名、UTF-8、缓存行为测试及完整套件。

分析：

这条轨迹的强项不是第一次修改就正确，而是能从“修改后仍失败”推断出 patch 位置和对象类型问题，随后改变测试方式并形成闭环。它同时验证了功能、回归和边界，因此过程质量很高。该轨迹的 `resolved=true` 与日志证据一致。

### 案例 B：失败轨迹

文件：`20260226_mini-v2.0.0_gemini-3-pro-high.json`

关键事件：

- 第 9 步精确复现原始错误，堆栈指向 `message.py:260` 的 `make_msgid(domain=DNS_NAME)`。
- 第 15 步只修改 `utils.py`，让 `socket.getfqdn()` 返回值做 IDNA 转换。
- 第 16 步再次运行原始 `reproduce_issue.py`，仍然失败；此时原测试直接 patch 的是 `django.core.mail.message.DNS_NAME`。
- 第 17-18 步随后改写测试，改为创建 `CachedDnsName` 并 patch `socket.getfqdn()`，该新场景通过。
- 第 30 步完整 mail 测试显示 `Ran 162 tests ... OK`。
- 最终标签仍为 `resolved=false`。

分析：

这条轨迹已经证明“真实 `CachedDnsName` 从 socket 获取 Unicode 主机名”可以工作，但没有解决第 16 步暴露的直接字符串 patch 场景，反而通过改变测试来得到通过结果。完整旧测试套件也没有覆盖这个新回归用例。因此，失败标签与轨迹内部证据并不矛盾：已有测试通过，但原始验收路径仍未闭合。这是“测试通过不等于任务完成”的典型案例。

## 4. 各条轨迹诊断

以下分数是依据上述 100 分细则对过程质量的估计；`resolved` 单独列出。

| 文件 | 模型 | 过程分 | `resolved` | 诊断摘要 |
|---|---|---:|---:|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | Claude Opus 4.5 | 88 | true | 先复现，再经历 `CachedDnsName` 类型错误和测试依赖问题，最终在 `message.py` 与 `utils.py` 处理 IDNA；完整 mail 测试和边界测试通过。早期尝试较曲折，但错误恢复充分。 |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | Claude Sonnet 4.5 | 80 | false | 定位准确，直接复现并验证 `str(DNS_NAME).encode('idna')`，精确 PR 场景及 157 个 mail 测试通过；但只改 `message.py`，未解决 SMTP 后端使用 Unicode `local_hostname` 的风险，且外部标签与日志不一致。 |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | Claude Opus 4.6 | 88 | true | 多次比较“改 utils、改 message、改 SMTP”的方案，识别预先存在的两个测试错误，最后保留源码改动并恢复测试文件；有错误断言和多次 checkout，但验证链完整。 |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | Gemini 3 Flash | 82 | true | 搜索范围较全，处理依赖安装、复现、message/utils 双点修复，运行 162 个 mail 测试及多个独立测试；存在大量临时脚本和若干畸形命令，流程噪声较大。 |
| `20260217_mini-v2.0.0_glm-5-high.json` | GLM-5 | 70 | true | 最终在 `CachedDnsName.get_fqdn()` 做 IDNA 转换，并通过原始、ASCII、已有 Punycode 和 162 个 mail 测试；但 75 步中有大量 `127` 畸形 shell 命令，几乎没有显式推理，过程效率和可读性较低。 |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | GPT-5.2 | 76 | true | 构造了可复用 `punycode()` 辅助函数，并在 message/utils 使用，精确复现成功；但 `rg`、`apply_patch` 不可用，未完成完整 mail 套件验证，`UnicodeError` 时 fallback 到 `localhost` 也缺少需求依据。 |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | GPT-5 mini | 58 | false | 发现并复现问题，先尝试改 utils，之后用 `FakeDns` 验证 `__str__`；但最终补丁只改 `utils.py`，直接 patch `message.py` 中已导入的 `DNS_NAME` 时不会转换，且没有完成修改后的完整测试。 |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | Kimi K2.5 | 86 | true | 先处理缺失依赖和 patch 对象不存在问题，再验证 `CachedDnsName`、精确 PR 示例、ASCII/混合域名和 162 个测试；修改集中在 utils，证据链较强。 |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | MiniMax 2.5 | 88 | true | 能根据第 11 步仍失败的反馈修正 patch 方式，最终验证缓存、IDNA、SMTP 相关行为及完整套件；临时脚本较多，但错误恢复和收尾质量好。 |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | Gemini 3 Pro | 62 | false | 原始错误复现清楚，utils 改动后旧测试 162 个通过；但第 16 步显示原始直接 patch 场景仍失败，后续通过改写测试绕开，因此过程与最终标签均提示修复未闭环。 |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | Gemini 3.5 Flash | 80 | true | 检查现有 IDNA 处理和 `DNS_NAME` 所有引用，完成 message/utils 修改并通过 162 个 mail 测试；没有先形成清晰的失败复现，且曾临时插入测试后再恢复，证据略弱于 Minimax/Kimi。 |

## 5. 可执行改进建议

1. **固定一条规范回归测试**  
   测试应同时覆盖：
   - 直接 patch `django.core.mail.message.DNS_NAME` 为 `"漢字"`；
   - patch `socket.getfqdn()` 让真实 `CachedDnsName` 返回 Unicode；
   - `iso-8859-1` 编码下 `Message-ID` 包含 `xn--p8s937b`；
   - ASCII 域名保持不变。

2. **明确转换边界和 API 契约**  
   最稳妥的方案应有一个明确的 IDNA 转换入口，并说明它处理的是：
   - `CachedDnsName` 对象；
   - 普通字符串；
   - SMTP `local_hostname`；
   - `Message-ID` 域名。  
   不应依赖调用者偶然触发 `__str__()`。

3. **避免用改变测试方式来“证明修复”**  
   如果修改后原始回归脚本仍失败，应优先修代码，而不是把测试改成另一个更容易通过的路径。

4. **测试命令不要用管道掩盖退出码**  
   `command | head`、`command | tail` 可能让 shell 返回管道最后一个命令的状态。应保存完整输出，或使用 `set -o pipefail`，并明确记录测试数量和最终状态。

5. **把环境问题与代码问题分开记录**  
   `asgiref`、`pytz`、`sqlparse`、`pytest` 缺失属于环境阻塞，不应被当作测试通过或代码失败。安装依赖后必须重新运行目标测试。

6. **提交前核对源码范围**  
   最终应检查：
   - `git status`；
   - `git diff --stat`；
   - 补丁是否只包含目标源码；
   - 临时测试脚本是否已删除；
   - 是否误修改测试、配置或生成文件。

7. **对 SMTP 后端进行显式评估**  
   `django/core/mail/backends/smtp.py` 使用 `DNS_NAME.get_fqdn()` 作为 `local_hostname`。即使本题主要表现为 `Message-ID` 崩溃，也应明确决定 SMTP EHLO 主机名是否同样需要 ASCII/IDNA 处理，并补充测试。

## 6. 不能仅凭这些轨迹确定的结论

- 不能确定三个 `resolved=false` 的精确外部失败原因。日志没有隐藏测试输出、补丁应用器反馈或评测器差异信息。
- 不能断言 Claude Sonnet 的代码一定错误：它的日志显示精确场景和 157 个测试通过，但可能存在 SMTP 范围、补丁提交或隐藏契约问题。
- 不能仅凭已有 157/162 个 mail 测试通过，证明新增 Unicode DNS 回归已覆盖；这些测试在多数轨迹中本来就没有该用例。
- 不能据此建立模型能力的普遍排名。样本只有一个 Django 任务，模型版本、上下文缓存、依赖环境和命令解析状态都不同。
- 不能从 token 数、步数或成本直接推断成功率。GLM-5 步数最多但成功，GPT-5 mini 步数最少之一却失败，说明关键在验证闭环和假设修正，而不是单纯的执行长度。
- 不能确认某些 fallback 行为（例如转换失败时返回 `localhost`）是否符合 Django 的正式设计要求；这需要项目维护者的 API 和兼容性判断。
