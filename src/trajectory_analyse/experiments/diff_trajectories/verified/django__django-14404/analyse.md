# 评测范围与判定

目录中共有 11 条 ATIF 轨迹，外部标签为 7 条成功、4 条失败。任务要求修复 Django `AdminSite.catch_all_view()` 对 `FORCE_SCRIPT_NAME` 的支持。

原始逻辑把同一个变量 `path` 同时用于：

1. 调用 `resolve()` 判断追加斜杠后的 URL 是否存在；
2. 构造 HTTP 301 重定向目标。

正确语义应当区分：

- `request.path_info`：不含脚本前缀，适合交给 URL resolver；
- `request.path`：包含 `SCRIPT_NAME` 或 `FORCE_SCRIPT_NAME`，适合生成面向客户端的重定向地址。

因此，理想修复应保持 `resolve('%s/' % request.path_info, ...)`，但将重定向目标改为 `'%s/' % request.path`，或使用等价的完整路径 API。

# 总体结论

## 1. 成功与失败的主要差异

成功轨迹普遍具备以下特征：

- 能定位到 `django/contrib/admin/sites.py:420` 附近的 `catch_all_view()`；
- 能检查 `HttpRequest.path`、`path_info` 以及 WSGI 请求如何拼接脚本名；
- 能认识到解析路径和重定向路径是两个不同问题；
- 至少有一种直接证据验证 `FORCE_SCRIPT_NAME` 下的 `Location`；
- 最终补丁通常只修改 `django/contrib/admin/sites.py`，并检查 diff。

失败轨迹主要分为两类：

1. **确实产生了语义错误的补丁**  
   `claude-4-5-opus-high`、`kimi-k2-5-high`、`minimax-2-5-high` 都把原来的：

   ```python
   path = '%s/' % request.path_info
   ```

   改成：

   ```python
   path = '%s/' % request.path
   ```

   这样不仅改变了重定向地址，也改变了 `resolve()` 的输入。脚本前缀被带入 resolver 后，通常会触发 `Resolver404`，导致本应发生的重定向变成 404。

2. **过程证据显示修复正确，但外部标签为失败**  
   `gemini-3-pro-high` 明确复现了 `/subdir/admin/login` 被错误重定向到 `/admin/login/`，随后只修改重定向返回值，复现测试和 342 个 `admin_views` 测试均通过，最终 diff 也是正确的。但 `compare.json` 仍标记为 `false`。这说明 `resolved` 可能受补丁提取、提交协议、隐藏评测或其他轨迹外因素影响，不能简单等同于代码过程是否正确。

## 2. 模型设置差异不能直接解释成能力排名

本样本每种模型设置基本只有一条轨迹，且还混有：

- 不同 mini-swe-agent 版本；
- 不同日期；
- 不同工具可用性；
- 不同依赖预装状态；
- 不同的命令失败和恢复路径。

因此不能据此断言某一模型稳定优于另一模型。例如：

- `gpt-5-mini` 仅 14 步、测试证据较少，但标签成功；
- `sonnet` 进行了 62 步大量探索，最终成功；
- `gemini-3-pro` 过程证据强但标签失败；
- `kimi` 和 `minimax` 都运行了既有测试，却因没有覆盖脚本前缀而保留错误实现。

更合理的结论是：本任务中，**是否显式区分 resolver 输入与 redirect 输出，以及是否进行带脚本前缀的回归测试**，比模型名称或“high”设置更能解释成败。

## 3. 证据强弱

证据强度从高到低大致如下：

1. 直接构造 `WSGIRequest`，检查 `request.path`、`path_info` 和响应 `Location`；
2. 针对 `AdminSiteFinalCatchAllPatternTests` 的测试结果；
3. 整个 `admin_views` 测试集结果；
4. `git diff` 中的最终代码；
5. 仅通过 grep、静态字符串检查或手写算术脚本推断。

本目录中多个轨迹的测试命令使用了 `2>&1 | head` 或 `| tail`，外层返回码可能为 0，即使前面的测试实际失败。因此测试证据需要结合输出内容判断，不能只看 `<returncode>0</returncode>`。

# 可复用的 100 分轨迹评分细则

| 维度 | 权重 | 满档 | 中档 | 低档 | 主要扣分条件与可观察证据 |
|---|---:|---|---|---|---|
| 任务理解与验收标准 | 15 | 明确指出 `FORCE_SCRIPT_NAME`、`path`/`path_info` 差异，并说明预期 `Location` | 识别脚本前缀问题，但未明确 resolver 与 redirect 的区别 | 只复述 PR 或机械替换字符串 | 未提及脚本前缀扣 4；把 `resolved` 当作过程正确性扣 3；错误理解目标扣 5-10 |
| 定位与技术假设 | 20 | 阅读 `catch_all_view`、WSGIRequest、resolver 或 CommonMiddleware，并形成可验证假设 | 定位到目标函数并查看部分相关代码 | 只搜索目标字符串，缺少因果分析 | 未检查 `path` 来源扣 5；错误认为 resolver 应使用完整脚本路径扣 8；无假设验证扣 3 |
| 代码修改正确性 | 25 | 保持 `path_info` 用于 `resolve()`，使用 `path` 或等价完整路径用于重定向，且修改最小 | 结果大体正确但有额外变量、注释或 API 风险 | 只改一处导致 resolver 语义改变，或修改无效 | 将 resolver 输入改为 `request.path` 扣 10-15；修改测试/配置扣 5；引入无关重构扣 2-4 |
| 测试与验证 | 25 | 有脚本前缀回归测试、无前缀测试、查询参数或边界测试，并运行相关 Django 测试 | 有针对性测试或既有测试，但覆盖不完整 | 只有静态检查、编译检查或未运行测试 | 没有 `FORCE_SCRIPT_NAME` 实测扣 8；只依赖既有测试扣 5；未调查失败测试扣 3；被管道吞掉状态扣 2 |
| 错误恢复与工具纪律 | 10 | 能识别依赖缺失、工具不可用、错误测试类名，并调整方案 | 能恢复部分环境问题，但过程较反复 | 忽略错误、重复错误命令或全局替换 | 全局 `sed` 替换扣 2；重复使用不存在的测试类扣 2；未处理依赖错误扣 2-4 |
| 最终收尾与证据链 | 5 | 最终 diff 只含必要源文件，补丁可读，并在提交前检查 | 有 diff 和补丁，但证据不完整 | 未确认最终代码、混入临时文件或无法判断提交内容 | 补丁包含无关文件扣 2；临时文件未清理扣 1；最终提交输出不可核验扣 1 |

设计上将代码正确性和验证合计设为 50 分，是为了避免“补丁看起来合理”或“既有测试通过”被误判为成功。任务理解和定位占 35 分，用于区分真正理解 Django 请求路径语义与机械替换。错误恢复和最终收尾权重较低，但仍保留分值，因为依赖问题、工具差异和补丁污染会直接影响可复现性。

# 具体案例

## 成功案例：`20260217_mini-v2.0.0_claude-4-5-sonnet-high.json`

关键事件：

- 步骤 3-11：定位 `catch_all_view()`，检查 `HttpRequest` 和 WSGIRequest 如何构造 `path` 与 `path_info`。
- 步骤 18：自定义复现脚本显示重定向缺少 `/admin-prefix`。
- 步骤 20：第一次把 `path` 变量改成 `request.path`，这一步实际上会破坏 resolver 输入。
- 步骤 34-51：继续检查 `resolve()` 的行为后意识到，resolver 仍需使用 `path_info`。
- 步骤 51：明确提出“resolve 使用 `request.path_info`，redirect 使用 `request.path`”。
- 步骤 60：最终 diff 同时引入 `path_info` 和 `path`，并使用正确的变量。
- 步骤 53、57：复现和测试均通过。

最终代码逻辑等价于：

```python
path_info = '%s/' % request.path_info
path = '%s/' % request.path
match = resolve(path_info, urlconf)
return HttpResponsePermanentRedirect(path)
```

该轨迹的价值不只是最终补丁正确，更在于它经历了错误假设、观察失败、重新检查 resolver 语义并修正。它是“错误恢复质量高”的典型。

缺点是过程较长，反复安装依赖，并有多个临时脚本；部分测试命令使用管道，返回码可信度有限。

建议评分：91/100。

## 失败案例：`20260217_mini-v2.0.0_kimi-k2-5-high.json`

关键事件：

- 步骤 3-9：正确定位函数，并通过自定义脚本确认 `path_info` 丢失脚本前缀。
- 步骤 10：一次定向 `sed` 命令失败。
- 步骤 11：随后使用全局替换：

  ```bash
  sed -i "s/request.path_info/request.path/" ./django/contrib/admin/sites.py
  ```

- 步骤 13：只通过读取源码确认“现在使用了 request.path”，没有验证 resolver 仍使用正确路径。
- 步骤 23-28：运行既有 admin 测试，日志声称 342 个测试通过。
- 最终 diff 把变量定义改成 `path = '%s/' % request.path`，因此 `resolve(path, urlconf)` 也接收了带脚本前缀的路径。

这条轨迹的过程问题是把“重定向目标正确”误化成“整个 `path` 变量都应使用 `request.path`”。既有测试没有设置 `FORCE_SCRIPT_NAME`，所以错误实现仍能通过 23 个或更大范围的既有测试。静态检查也只检查字符串是否替换成功，没有验证行为。

建议评分：52/100。

## 标签矛盾案例：`20260226_mini-v2.0.0_gemini-3-pro-high.json`

关键事件：

- 步骤 17：直接复现得到：

  - `request.path = /subdir/admin/login`
  - `request.path_info = /admin/login`
  - 实际 `Location = /admin/login/`
  - 预期 `Location = /subdir/admin/login/`

- 步骤 20：只将 `HttpResponsePermanentRedirect(path)` 改为使用 `request.path`。
- 步骤 22：复现成功，`Location` 包含 `/subdir`。
- 步骤 24：`admin_views` 342 个测试通过，跳过 17 个。
- 步骤 28：最终补丁只修改 `django/contrib/admin/sites.py`，内容与预期修复一致。

从过程质量和代码证据看，该轨迹应被评为高质量；但 `compare.json` 将其标为 `false`。可能原因包括隐藏测试差异、补丁提交/提取问题或外部评测流程问题。仅凭这条轨迹无法判断具体原因，也不能把该标签直接解释成代码错误。

建议过程评分：92/100；最终 `resolved`：false；标签一致性：低。

# 各条轨迹诊断摘要

| 轨迹文件 | 步数 | 过程质量评分 | 过程质量诊断 | 最终 `resolved` |
|---|---:|---:|---|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | 23 | 55 | 定位和理解基本正确，但把 `path` 变量整体改为 `request.path`；自定义测试先因依赖失败，后只验证源码和无脚本前缀测试，未发现 resolver 回归 | false |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | 62 | 91 | 先犯同类错误，随后通过复现和 resolver 分析恢复；加入脚本前缀、边界和 admin 测试，最终 diff 正确；过程偏冗长 | true |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | 20 | 88 | 初次修改不正确，主动识别并恢复；最终将 resolver 内联使用 `path_info`、重定向使用 `path`，23 个目标测试通过 | true |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | 36 | 86 | 通过复现发现脚本前缀和查询参数问题，最终使用 `get_full_path(force_append_slash=True)`；测试覆盖较广，但多次命令失败且 ATIF 中缺少文本推理 | true |
| `20260217_mini-v2.0.0_glm-5-high.json` | 59 | 82 | 大量环境和设置排错，构造了脚本前缀、resolver 和边界测试，最终最小修复并运行 admin 测试；过程较曲折，文本推理大多缺失 | true |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | 24 | 78 | 能明确区分 resolver 与 redirect；`rg`、`apply_patch` 不可用后改用 grep/Python；编译和自定义复现通过，但没有完整 Django 测试 | true |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | 14 | 63 | 定位和最终补丁正确，但 `git apply` 失败后用 Python 修改，导入测试因依赖失败，基本没有行为级回归验证；还执行了 `git add -A` | true |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | 32 | 52 | 复现确认问题，但全局替换破坏 resolver；错误测试类名和缺少脚本前缀回归导致既有测试通过却未发现缺陷 | false |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | 30 | 54 | 检查了 flatpages 等相关代码，然而同样把变量整体换成 `request.path`；自定义检查较弱，既有 23 个目标测试通过不足以证明修复 | false |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | 29 | 92 | 有最清晰的直接复现、正确的最小修改、342 个 admin 测试和额外综合测试；过程证据与外部标签矛盾 | false |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | 44 | 90 | 系统检查 WSGI、resolver、CommonMiddleware 和既有测试，加入临时回归测试后清理，目标类和 admin suite 通过，最终最小 diff 正确 | true |

ATIF 转换后，`gemini-3-flash`、`glm-5`、`gemini-3-pro` 等记录的大部分 `message` 和 `reasoning_content` 为空，因此这些轨迹的“思考过程”只能依据工具调用和观察结果评估，评分置信度低于文本完整的轨迹。

# 可执行的改进建议

1. **把验收条件写成行为断言**  
   每条轨迹都应明确验证：

   ```text
   request.path_info = /admin/login
   request.path = /subdir/admin/login
   预期 Location = /subdir/admin/login/
   ```

2. **在修改前固定两个独立变量的职责**  
   建议先写出：

   ```python
   resolve_path = '%s/' % request.path_info
   redirect_path = '%s/' % request.path
   ```

   再修改代码，避免全局字符串替换。

3. **至少运行三类测试**  
   - `FORCE_SCRIPT_NAME` 开启时的直接回归测试；
   - 不启用脚本前缀时的兼容性测试；
   - `AdminSiteFinalCatchAllPatternTests` 或整个 `admin_views` 测试集。

4. **测试失败必须区分环境失败和代码失败**  
   `asgiref`、`pytz`、`sqlparse` 缺失属于环境问题；`Resolver404`、错误 `Location` 属于代码问题。两者不能用同一个“测试失败”结论代替。

5. **避免依赖管道后的外层返回码**  
   `command | tail` 可能掩盖前一命令失败。应使用 `set -o pipefail`，或者直接查看测试末尾的 `FAILED`、`OK`、`Ran N tests`。

6. **测试类名应从源码确认**  
   多条轨迹使用了不存在的 `AdminViewCatchAllTests` 或 `AdminFinalCatchAllViewTests`，浪费步骤并降低验证可信度。应先读取实际类名 `AdminSiteFinalCatchAllPatternTests`。

7. **最终补丁检查应同时检查语义和范围**  
   只确认 diff 只有一个文件还不够，还应确认：
   - `resolve()` 仍接收 `path_info`；
   - 重定向使用 `path`；
   - 没有临时测试文件；
   - 没有意外暂存或修改其他文件。

8. **外部评测应记录补丁提取状态**  
   `gemini-3-pro` 的过程证据和最终代码均正确但标签为 false，说明评测系统还需要记录：
   - 实际提交的 patch 内容；
   - 隐藏测试失败信息；
   - patch 是否成功解析；
   - 轨迹结束时工作区状态。

# 不能仅凭这些轨迹确定的结论

- 不能据此建立模型能力排名，因为每个模型设置样本量基本为 1。
- 不能确定 `gemini-3-pro` 的 false 是隐藏测试失败、补丁提取失败，还是评测标签错误。
- 不能仅凭既有 `admin_views` 测试通过断言 `FORCE_SCRIPT_NAME` 已被覆盖；失败轨迹已经证明无脚本前缀测试会放过错误实现。
- 不能确定 `get_full_path(force_append_slash=True)` 是否完全等价于 PR 期望的 `'%s/' % request.path`，尤其是查询参数、编码和特殊路径的行为需要独立测试。
- 不能仅凭工具命令返回码判断测试成功，因为多条轨迹使用了会吞掉失败状态的 shell 管道。
- 不能从 ATIF 中缺失的 `message` 或 `reasoning_content` 推断模型没有进行推理，只能说该部分过程证据不可观察。
