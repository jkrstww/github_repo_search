# Django `django__django-15732` 轨迹评测报告

## 一、评测范围与判定原则

共审阅 11 个 ATIF JSON 轨迹，外部结果为 4 个 `resolved=true`、7 个 `resolved=false`，成功率 36.4%。

本报告将两个概念严格分开：

- **过程质量**：任务理解、定位、工具使用、实现、测试、恢复和交付证据。
- **最终结果**：用户给出的外部 `resolved` 标签。该标签不参与过程评分，只用于事后校准。

问题的核心不是简单地“排除主键”，而是：同一列可能同时存在主键、字段级 `unique=True` 和 `unique_together` 产生的约束；删除时必须识别并只删除 `unique_together` 对应的约束，同时保留其他唯一性约束。

## 二、总体结论

最强的样本内分界是：

- 4 条成功轨迹最终都加入了某种**约束名消歧**：精确匹配 Django 生成的 `_uniq` 名称，或至少按 `_uniq` 后缀筛选。
- 6 条有实际补丁但失败的轨迹，最终都只做了 `primary_key=False`，因此只能排除主键，不能区分普通字段级 `unique=True` 与同列 `unique_together`。
- 剩余 1 条失败轨迹停留在代码阅读阶段，没有修改、测试或提交。

这一分界与 `resolved` 标签完全一致，是本样本中最强的相关证据；但由于没有隐藏测试日志，不能断言这就是每条外部失败的唯一原因。

成功轨迹的共同优势不是模型更“长思考”，而是把筛选条件从“约束属性相同”提升为“约束身份正确”。步骤数并无单调关系：GPT-5.2 用 24 步成功，GLM-5 用 92 步成功；Gemini Pro 和 Kimi 分别用了 53、60 步仍失败。

测试数量也不是决定因素。若只跑既有 schema/migration 套件，原有覆盖缺口仍可能让错误补丁全部变绿。Minimax 在 310 个既有测试通过前，已经亲自证明普通 `unique=True` 场景仍失败，却错误地将其排除出任务范围。

证据强度分级如下：

- **强证据**：成功/失败补丁在“是否进行名称消歧”上完全分离；多个轨迹直接复现了两个唯一约束并观察到错误。
- **中等证据**：Sonnet、GLM-5、Gemini 3.5 Flash 有修改前后目标测试和广泛回归测试。
- **弱证据**：多数 PostgreSQL 行为由 SQLite introspection 改写或 mock 模拟；没有真实 PostgreSQL 验证，也没有外部评测日志。

## 三、可复用的 100 分轨迹评分细则

| 维度 | 权重 | 评分档位 | 主要扣分条件与可观察证据 |
|---|---:|---|---|
| 任务理解与验收条件 | 15 | 13–15：明确列出冲突约束及保留不变量；8–12：只覆盖主描述；0–7：仅复述问题 | 忽略标题中的普通 `unique=True`、未说明不能误删 PK/字段唯一约束，扣 3–8 分。证据来自推理文本、测试矩阵。 |
| 定位与因果假设 | 15 | 13–15：贯通 `alter_unique_together → _delete_composed_index → _constraint_names/introspection`；8–12：定位正确但解释不完整；0–7：猜测式修改 | 是否读取 PostgreSQL introspection、约束生成命名和相邻调用者。 |
| 工具与命令质量 | 10 | 9–10：搜索聚焦、使用项目测试入口；5–8：有冗余但可恢复；0–4：长输出、错误工具或高风险命令主导 | 不必要安装、`git add -A`、反复生成脚本、错误的 `read_file`/测试入口均扣分。 |
| 实现正确性与范围 | 25 | 22–25：精确删除目标且保持兼容；14–21：主路径正确但有脆弱启发式或额外改动；0–13：已知目标场景仍失败或无补丁 | 精确名称匹配优于任意 `_uniq` 子串；误删第一个匹配项、只排除 PK、修改无关路径均扣分。 |
| 测试与验证证据 | 20 | 18–20：失败复现、修复后验证、回归和后端差异均覆盖；10–17：部分覆盖；0–9：仅编译、既有测试或无测试 | 应检查删除后目标约束消失且其他唯一约束仍存在。没有行为测试时本项最高 6 分。 |
| 错误恢复与证据响应 | 10 | 9–10：利用失败修正假设；5–8：能修复环境/编辑错误；0–4：忽视或解释掉反证 | 目标测试失败后仍提交同类补丁属于严重扣分。 |
| 收尾与可复现交付 | 5 | 5：清理临时文件、检查状态和 scoped diff、按要求提交；3–4：有轻微遗留；0–2：未提交或补丁不明 | 观察 `git status`、`patch.txt` 内容及最终提交命令。 |

总分解释：85–100 为高质量，70–84 为实现较强但证据或范围仍有风险，50–69 为部分正确，低于 50 为明显不完整。评分存在约 ±3 分人工判断误差。

额外规则：流水线如 `runtests ... | head/tail/grep` 未启用 `pipefail` 时，外层返回码 0 不能单独证明测试成功；必须同时观察 `Ran N tests` 和最终 `OK`。`resolved` 不得作为某一过程维度的加分依据。

## 四、逐条轨迹诊断

| 轨迹文件 | `resolved` | 过程分 | 过程质量与结果诊断 |
|---|---:|---:|---|
| `20260217_mini-v2.0.0_claude-4-5-opus-high.json` | false | 63 | 正确定位并用 mock 模拟 PostgreSQL PK，但最终只传入 `primary_key=False`；schema/migration 测试无法覆盖普通 `unique=True` 冲突。过程尚可，补丁语义不足，失败结果一致。 |
| `20260217_mini-v2.0.0_claude-4-5-sonnet-high.json` | true | 88 | 真实复现字段级双唯一约束，经历 malformed patch 和缩进错误后恢复备份，最终用排除 PK 加 `_uniq` 名称筛选；目标测试、180 个 schema 和 130 个 migration 测试通过。过程强，但后缀启发式仍有跨后端风险。 |
| `20260217_mini-v2.0.0_claude-4-6-opus.json` | false | 60 | 调用链定位准确，也口头注意到普通 `unique=True`，实际却只在通用 `_delete_composed_index()` 中加入 `primary_key=False`。曾把“运行 0 个测试”视为成功，后续虽跑通 180+130 个既有测试，仍无目标复现。 |
| `20260217_mini-v2.0.0_gemini-3-flash-high.json` | false | 20 | 9 个事件仅搜索到 `_delete_composed_index()`，无实质推理、修改、测试或提交。属于未完成轨迹，而非错误补丁。 |
| `20260217_mini-v2.0.0_glm-5-high.json` | true | 87 | 先 mock PostgreSQL PK，再复现普通 `unique=True` 仍失败，据此加入 `_uniq` 后缀消歧；目标场景、310 个 schema/migration 和 280 个 backend 测试通过。证据强，但 92 步明显冗长，后缀筛选较脆弱。 |
| `20260217_mini-v2.0.0_gpt-5-2-high.json` | true | 76 | 实现质量较高：精确计算期望名称、进行 identifier normalization，并提供 PK 与后缀回退；但除 `compileall` 外没有任何行为测试。外部成功说明补丁被接受，不足以弥补轨迹内验证证据缺失。 |
| `20260217_mini-v2.0.0_gpt-5-mini.json` | false | 43 | 定位正确，但补丁只做通用 PK 排除；首次 `git apply` 失败，之后使用 `git add -A` 导致空 patch 再 reset。完全没有测试，过程和最终结果均弱。 |
| `20260217_mini-v2.0.0_kimi-k2-5-high.json` | false | 58 | 花费大量步骤处理依赖和测试入口，最终跑通 179 个 schema 与 migration 测试；但自建复现曾返回“找到 0 个约束”，未形成可信验收，最终仍是仅排除 PK 的补丁。 |
| `20260217_mini-v2.0.0_minimax-2-5-high.json` | false | 60 | PK 目标测试和 310 个回归测试通过，但普通 `unique=True` 测试明确报“Found wrong number (2)”后，被错误认定为“另一个问题”。这是证据充分但任务边界判断错误的典型失败。 |
| `20260226_mini-v2.0.0_gemini-3-pro-high.json` | false | 66 | 成功 mock 出 PostgreSQL PK 问题，也复现普通字段双约束；尝试放宽数量检查后实际删除了错误的 unnamed constraint，随后回退，却仍提交已知不完整的 PK-only 修复。诊断价值高，收敛失败。 |
| `20260901_mini-v2.4.2_gemini-3-5-flash.json` | true | 89 | 临时加入 PK 与普通 `unique=True` 两个测试，修改前观察到后者失败，随后按 Django 期望名称精确筛选；两个目标测试及 182 个 schema 测试通过，并恢复测试文件、检查状态后提交。额外修改 `_alter_field` 扩大了范围，且无真实 PostgreSQL验证。 |

成功组平均过程分约 85，失败组约 52.9。该差距主要来自目标覆盖、实现消歧和对反证的响应，而不是 `resolved` 被纳入评分。

## 五、关键案例

### 成功案例：Gemini 3.5 Flash

在 [`20260901_mini-v2.4.2_gemini-3-5-flash.json`](/Users/zzk/project/github_repo_search/src/trajectory_analyse/experiments/diff_trajectories/verified/django__django-15732/20260901_mini-v2.4.2_gemini-3-5-flash.json) 中：

- `step_id=43`：新增的普通 `unique=True` 测试稳定复现 `Found wrong number (2)`。
- `step_id=47`：除 `primary_key=False` 外，计算 `_create_index_name(..., suffix="_uniq")`，仅在候选中选择精确期望名称。
- `step_id=48`：两个目标测试均通过；`step_id=49`：182 个 schema 测试通过。
- `step_id=52–56`：恢复临时测试、确认仅源文件修改、检查并提交 patch。

这些证据建立了“修改前失败 → 有针对性的实现 → 修改后通过 → 回归与交付检查”的完整链条。外部 `resolved=true` 与过程证据相互支持，但额外 `_alter_field` 改动仍需要独立回归证明。

### 失败案例：Minimax 2.5

在 [`20260217_mini-v2.0.0_minimax-2-5-high.json`](/Users/zzk/project/github_repo_search/src/trajectory_analyse/experiments/diff_trajectories/verified/django__django-15732/20260217_mini-v2.0.0_minimax-2-5-high.json) 中：

- `step_id=34`：修复后数据库仍存在两个非 PK 唯一约束，并报 `Found wrong number (2)`。
- `step_id=35`：模型将该反例称为“different issue”，与标题中的 “single field with its own unique=True constraint” 直接冲突。
- `step_id=36`：310 个既有测试通过，但这些测试并未否定刚刚出现的目标失败。
- 最终仍提交仅增加 `primary_key=False` 的补丁，`resolved=false`。

该案例说明：目标测试的反证比大规模但不覆盖缺口的绿色回归更重要。失败原因主要是错误解释证据，而非定位能力不足。

### `resolved=true` 但证据较弱：GPT-5.2

[`20260217_mini-v2.0.0_gpt-5-2-high.json`](/Users/zzk/project/github_repo_search/src/trajectory_analyse/experiments/diff_trajectories/verified/django__django-15732/20260217_mini-v2.0.0_gpt-5-2-high.json) 的实现包含最完整的精确名称、大小写规范化和 PK 回退逻辑，因此获得外部成功；但整个轨迹没有运行行为测试，仅执行 `compileall` 和 diff 检查。它说明 `resolved=true` 可以证明本次外部评测通过，却不能证明开发过程拥有充分的可审计验证证据。

## 六、可执行改进建议

1. 修改前建立验收矩阵：PK+`unique_together`、普通 `unique=True`+`unique_together`、多列 `unique_together`、同列显式 `UniqueConstraint`、`index_together`，并验证非目标约束仍存在。
2. 将“约束身份”作为实现核心：先排除 PK，再通过 Django 的确定性生成名称和 backend `identifier_converter` 精确匹配；仅靠“第一个候选”或任意 `_uniq` 子串不够稳健。
3. 至少在 SQLite 和 PostgreSQL 上运行目标测试。SQLite 将 PK 报告为 `unique=False`，无法自然复现描述中的 PostgreSQL行为；mock 只能作为补充。
4. 使用项目标准入口 `tests/runtests.py`；避免用 `head/tail/grep` 隐藏真实退出码，或显式启用 `set -o pipefail`。
5. 把目标测试失败视为停止提交的条件。不能因为既有大套件通过，就将字面需求中的失败场景重新定义为题外问题。
6. 控制补丁范围：对 `_alter_field`、`index_together` 等相邻路径的修改必须有对应测试，否则会扩大回归面。
7. 收尾统一执行：目标测试、相关模块回归、语法检查、`git status`、scoped diff、临时文件清理和最终 patch 检查。

## 七、无法仅凭这些轨迹确定的结论

- 无隐藏测试输出，不能确定每个 `resolved=false` 的精确触发断言，也不能排除提交提取或环境差异。
- 单个 Django 任务、每个模型一次运行，不能推出模型的一般能力排名或统计显著优势。
- 多数轨迹没有真实 PostgreSQL、MySQL 或 Oracle 执行证据，无法确认名称截断、大小写转换、表重命名和约束类型差异下的完整正确性。
- `resolved=true` 不能证明没有潜在回归，尤其是 Sonnet/GLM 的后缀启发式和 Gemini 3.5 Flash 的额外 `_alter_field` 修改。
- 轨迹由不同日期和 mini-swe-agent 版本生成，环境依赖、缓存、成本限制和代理版本都可能构成混杂因素。
- ATIF 中部分命令输出被截断，最终提交步骤通常没有 observation；因此可以确认代理发出了提交命令，但不能仅凭轨迹证明外部系统如何解析和执行了该补丁。
