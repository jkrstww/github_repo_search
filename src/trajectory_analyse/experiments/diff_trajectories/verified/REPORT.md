# 轨迹打分细则与 resolved 结局相关性的验证报告

> 本报告用 LLM-as-judge 方式,基于 `trajectory_scoring_prompt.md` 的多维度评分细则,对
> SWE-bench Verified 的真实 agent 轨迹过程进行盲打分,并验证打分与轨迹真实结局
> (`resolved`)的相关性、跨判一致性,以及评分细则各维度的判别贡献。
>
> 评审模型:`GLM-5`(主判,966 对全量)与 `deepseek-v4-flash`(辅判,260 对,因配额耗尽)
> 两套独立通道;两项结果在重叠样本上做 ICC 对比。

---

## 1. 背景与问题

此前用大模型分析 agent trajectory,产出一套「轨迹中间过程打分细则」
(`trajectory_scoring_prompt.md`),按 A–F 六个维度对单条轨迹的过程质量评 0–100 分。

本次验证要回答三个问题:

1. **判别力** —— 这些过程分能否预测轨迹的最终结局(`resolved`)?
2. **稳健性** —— 消除「题目难度」混淆后,信号是否仍成立?
3. **可信度** —— 不同 LLM judge 给出的分数是否一致?评分细则里
   哪些维度客观可重复、哪些主观噪声大?

## 2. 方法

### 2.1 数据底座

- 取 `experiments/diff_trajectories/verified/` 下「同一 instance、跨模型 `resolved`
  不一致」的轨迹:100 个 instance × 10 个模型提交 = **1000 对**。
- 标签来自 `compare_verified.json`(已在预处理中剔除已知全失败的
  `20260226_mini-v2.0.0_gemini-3-pro-high` 提交,保留 100 实例 × 10 模型),
  其中 `resolved` True/False ≈ 582/406,分布均衡。
- 该底座的关键性质:**每个 instance 内部天然同时有 resolved=True 与 False 的轨迹**,
  为「实例内配对」分析提供了基础。
- 局限:**所有提交均来自 `mini-swe-agent` harness**(仅模型不同),故结论
  适用于「mini-swe-agent 家族在 SWE-bench Verified 上的过程质量」,不可外推到任意 harness。

### 2.2 打分管线(score_trajectories.py,纯标准库)

- **盲打分**:judge 只看单条 `trajectory.json`,从不接触 `resolved` 标签 ——
  标签仅在后续 `build_scores.py` 联表时引入。这是整个验证的前提:若 judge 看着结局打分,
  相关就是循环论证。
- **隔离**:codex 通道把轨迹复制进临时空目录再调用(防读到同 instance 的其他轨迹或遗留
  `analyse.md`);API 通道把轨迹 JSON 内联进单条 user message。
- **严格产出**:judge 按细则输出严格 JSON,脚本校验 `total_score == Σ(A..F)` 才视为合法;
  失败写占位并记入 `scoring_failures.jsonl`,可续跑(resume)。
- 全套 83 个 unittest 通过;纯标准库实现(仓库 `pyproject.toml` 声明 `dependencies = []`),
  运行于 conda Python 3.13。

### 2.3 评审模型与跑批情况

| 评审模型 | 通道 | base_url | 结果 |
|---|---|---|---|
| `GLM-5`(主) | `--judge-backend api` | `https://antchat.alipay.com/v1` | **966 对合法**(97%)、100 instance 全覆盖 |
| `deepseek-v4-flash`(辅) | `--judge-backend api` | `https://api.deepseek.com` | 260 对合法(26%,**账户配额耗尽中断**) |

> 跑分过程中修掉的生产 bug:GLM-5 在 `response_format=json_object` 模式下吐
> `{{...}}` 双花括转义 JSON → 改用 `--no-api-json-mode`;网关 `RemoteDisconnected`
> 瞬态抖动 → `_http_json` 加内置带退避重试;`build_scores`/`_load_existing` 把
> 全 0 占位误当合法 → 改为同时要求 `_meta.valid==True`。这些都在 unittest 中回归。

### 2.4 统计方法

`resolved` 是布尔、`total_score` 是 0–100 连续,**不能简单套相关系数**。采用:

- **主信号:实例内配对 Δ**:每个 instance 内算 `Δ = mean(分|True) − mean(分|False)`,
  消掉 instance 难度后,汇总 100 个 Δ;用符号检验 + Wilcoxon signed-rank。
- **全局判别**:AUC-ROC、PR-AUC、point-biserial r、Mann-Whitney U;
  CI 用按 instance 聚类的 bootstrap(因子非独立:同 instance 被多模型重测)。
- **维度分解**:纯过程维(A 任务理解 + B 根因 + E 纪律)vs outcome 同义维
  (C 实现 + D 测试 + F 收尾,**含与 resolved 定义同义的封顶**)vs 全量,分别算 AUC,
  用以分离「真过程信号」与「封顶带来的同义贡献」。
- **跨判一致性**:DeepSeek × GLM-5 在 259 个重叠对上算 ICC(2,1)。

## 3. 结果

### 3.1 主信号:实例内配对(GLM-5,100 个 instance)

100 个 instance(每个同时含 True/False)内配对的 Δ:

| 指标 | 值 |
|---|---|
| Δ 均值 | **+8.95** |
| Δ>0 的 instance 比例 | **74%**(74 正 / 26 负) |
| 符号检验 p | **1.7 × 10⁻⁶** |
| Wilcoxon signed-rank p | **4.5 × 10⁻⁹** |

→ **在消掉「题目难度」后,过程分确实显著偏向给修成功的轨迹更高分**;
且方向稳健 —— 74% 的题里分高的更可能是 resolved。这是本验证最干净的证据。

### 3.2 全局判别力(GLM-5,966 对)

| 指标 | 值 | 解读 |
|---|---|---|
| AUC-ROC | 0.635(CI 0.59–0.67) | 显著高于 0.5,但**偏弱** |
| PR-AUC | 0.672(base rate 0.588) | 略高于 base rate |
| point-biserial r | 0.253(CI 0.18–0.33) | 弱-中等正相关 |
| 分组均值 True/False | 77.99 / 69.73 | 差约 8 分,但**重叠不小** |
| Mann-Whitney | U z=7.16, p<0.001, effect=0.27 | 两组分布确实不同 |
| Youden 阈值 | threshold=80.5,balanced acc=0.61(TPR 0.54 / TNR 0.67) | 阈值偏高,且找不到把所有失败都抓干净的点 |

直观印象:分数与结局**有单调关系但判别力有限** —— 总分均值整体偏高(全样
本 mean=74.6 / median=79 / max=100),resolved=False 的也常拿 60+,
靠阈值硬切会大量误分。

### 3.3 逐维度 point-biserial(GLM-5)

| 维度 | r | CI95 |
|---|---|---|
| A 任务理解 | 0.199 | [0.14, 0.26] |
| B 根因定位 | 0.222 | [0.15, 0.29] |
| C 实现正确性 | 0.240 | [0.17, 0.31] |
| D 测试证据 | 0.237 | [0.16, 0.31] |
| E 执行纪律 | 0.204 | [0.13, 0.27] |
| F 收尾卫生 | 0.232 | [0.16, 0.30] |

六维 r 都在 0.20–0.24,**没有任何一维脱颖而出**,也不存在明显的「短板维度」。

### 3.4 维度分解:过程信号 vs outcome 同义

| predictor | AUC-ROC | CI95 |
|---|---|---|
| 纯过程维 A+B+E | 0.622 | [0.585, 0.661] |
| outcome 同义维 C+D+F | 0.637 | [0.596, 0.680] |
| 全量 total | 0.635 | [0.595, 0.678] |

三者 AUC **几乎一样**(差 ≤0.015)。这说明:

- rubric 中那些与 `resolved` 定义同义的封顶规则(空 patch → ≤49、运行错误仍在 → ≤59、
  无修后验证 → ≤74 等)并**没有带来明显的判别增量**;
- 即相关并非「靠封顶钩子硬凑出来的」,而是**六个维度整体弱相关**共同贡献;
- 但也意味着 rubric **没体现独立的过程增量信号** —— 过程维(A+B+E)与
  outcome 同义维(C+D+F)预测力相当,没有「某个量表专门抓过程」的差异化能力。

### 3.5 跨判一致性(GLM-5 × DeepSeek,259 对重叠)

| | total | A | B | C | D | E | F |
|---|---|---|---|---|---|---|---|
| ICC(2,1) | **0.64** | 0.47 | 0.48 | 0.49 | **0.79** | 0.58 | 0.41 |
| Pearson | 0.72 | 0.56 | 0.65 | 0.54 | **0.80** | 0.64 | 0.55 |

- 两个**完全不同家族**的 judge 在总分上 ICC=0.64、Pearson=0.72 ——
  排除了「单一 judge 自说自话」,判别信号具有跨判可重复性。
- **D 维(测试证据)ICC=0.79 / Pearson=0.80 最高** —— 这恰好是 rubric 里
  最客观可观察的维度(有没有跑测试、有没有断言、red→green),最易在 judge 间达成一致。
- A/B/F 一致性偏低(ICC 0.41–0.49)—— 任务理解、根因判断、收尾这些维度主观成分大,
  judge 间分歧大、噪声高。

### 3.6 过程诊断副产物:caps_or_flags 频率

| flag | 次数 | 含义 |
|---|---|---|
| `NO_BASELINE` | 266(占 28%) | 绝大多数轨迹**没有修前失败基线** |
| `NO_POST_FIX_TEST` | 179(19%) | 修后无可执行验证 |
| `KNOWN_COUNTEREXAMPLE` | 53(5%) | 已发现反例却未修仍收尾(高风险) |
| `UNRELATED_CHANGES` | 19 / `MAIN_CASE_ONLY` | 12 / `EMPTY_PATCH` 11 等 | 卫生问题 |

→ 一个隐藏信号:**28% 轨迹缺修前基线、19% 缺修后验证**,说明分数里相当部分
被「证据齐全度」而非「代码对错」决定 —— 这是 rubric 把握的现实约束
(轨迹本就没留下可验证证据),也提示分数的信噪比上限受限于此。

## 4. 有效性限制(必须写进结论)

1. **Outcome 同义部分** —— rubric 的封顶规则与 `NOT-resolved` 定义部分同义,
   C/D/F 因此与 resolved 有构造性相关;但 §3.4 表明这部分**增量极小**,
   相关主要来自六维整体,而非封顶钩子。
2. **单 judge 同构造嫌疑** —— 两个 judge 都是 LLM,可能存在共同的系统性偏好;
   ICC=0.64 排除了「自说自话」,但未必排除「同一类模型共有的偏见」。
   建议补少量**人工盲打分**做三角验证(human↔judge、human↔resolved)。
3. **Selection / harness 偏差** —— 全部 100 个实例、所有提交均来自 mini-swe-agent
   家族,且取的是「跨模型 resolved 不一致」子集;base rate ≈ 0.59,AUC 上限受限,
   结论不可外推到其他 harness / 全集。
4. **DeepSeek 未跑满** —— 辅 judge 仅 260 对(40%,按 instance 29 个);
   辅判的全局 AUC(0.633)与主判(0.635)高度吻合是积极的,但辅判
   缺的那 60 个 instance 不参与 ICC 计算,ICC 仅基于 259 对重叠样本。
5. **分数整体偏高、阈值难定** —— mean 74.6、median 79,失败轨迹也常得高分,
   balanced acc 最高 0.61;rubric 作「质量分级」尚可,作「成功/失败分类器」不够锐利。
6. **D 维过强 = 过程维过弱的危险信号** —— D(测试证据)既是判别力最强的 r(0.24),
   又是跨判最一致的 ICC(0.79)。这意味着相关性很大程度由「测试有没有跑过」驱动,
   而不是「根因判断是否准确、实现是否最小化」等更纯粹的过程质量。

## 5. 结论

- **过程分与结局显著正相关,但偏弱**:实例内配对 Δ=+8.95、p<1e-6
  (§3.1)是消难度后最干净的证据;全局 AUC 0.635(§3.2)单看不算强判别器。
- **信号可重复**:跨家族 judge ICC=0.64(§3.5),说明 rubric 测的东西有
  非自说自话的一致性。
- **rubric 没体现过程增量**:过程维与 outcome 同义维 AUC 几乎相同(§3.4)、
  六维 r 全部均匀弱相关(§3.3)—— 相关性来自六维整体,没有任何「专门抓过程」的亮点维度。
- **判别力主要由客观证据维(D,测试证据)驱动**(§3.5 + §3.6):
  这既是好处(可重复),也是信号 —— rubric 对「真正的过程质量」
  (根因理解、实现优雅、收尾卫生)的捕捉力有限,更多反映了
  「证据齐全度」(NO_BASELINE 占 28%)。

**一句话**:在 mini-swe-agent × SWE-bench Verified 上,这套打分细则
**确实显著优于随机且跨判稳定**,能作为轨迹质量的**排序参考**;但要作为
成功的**强预测器或可信分类阈值,判别力不够**,且对「纯粹过程质量」
的覆盖弱于对「客观证据齐全度」的覆盖。

## 6. 可改进方向

- 把 D 维降权 / 拆分「测试是否跑过」与「测试设计质量」,避免「跑过测试」这种
  客观事实绑架整个 D 维;
- 提高 A/B 维判窄程度并配套反例矩阵要 judge 必填,做主客观信号的不耦合;
- 补人工 spot-check(30–50 例),建立 human↔judge ICC,给 LLM judge 上限定锚;
- 在更多 harness(非 mini-swe-agent)上复测,扩 selection 外推域。

---

## 附录:数字总览(供后续引用)

| 项 | 值 |
|---|---|
| 打分样本 | 966 对 / 100 instance(GLM-5);259 对重叠算 ICC |
| resolved T/F | 568 / 398(base rate 0.588) |
| 全局 AUC-ROC | 0.635(CI95 [0.59, 0.67]) |
| 实例内 Δ | +8.95,符号检验 p=1.7e-6,Wilcoxon p=4.5e-9 |
| 实例内 Δ>0 比例 | 74%(74 正 / 26 负) |
| 跨判 ICC(total) | 0.64 / Pearson 0.72 |
| 维度分解 AUC | A+B+E 0.622,C+D+F 0.637,full 0.635 |
| 总分分布 | min 10 / median 79 / mean 74.6 / max 100 |
