# 轨迹打分 vs resolved 相关性报告
- 可打分 pair 数: 260; resolved True/False = 155/105; base rate = 0.5962
- 抽样单元: 非独立的 (instance × model) 对; 主信号=实例内配对 Δ(cluster-bootstrap CI 聚类于 instance)

## 1. 主信号:实例内配对 (primary)
- 在 26 个同时含 True/False 的 instance 内计算 Δ = mean(分|True) - mean(分|False)
- Δ 均值 = 10.0059; Δ>0 的 instance 比例 = 0.7692
- 符号检验: +20/-6/0×0 (n=26, p=0.009355306625366211)
- Wilcoxon signed-rank: W+=299.5, z=3.1367766565249626, p=0.001708161949557807
> 同题内消掉实例难度后才回答「分高的 run 更可能是 resolved 的吗」。

## 2. 全局判别力 (cluster-bootstrap CI 聚类于 instance)
- AUC-ROC = 0.6327  CI95 = [0.5629, 0.7033]
- PR-AUC = 0.6703  (base rate = 0.5962)
- point-biserial r = 0.2813  CI95 = [0.1543, 0.4167]
- 分组均值: True=71.5226, False=62.2876
- Mann-Whitney: U=10297.5, z=3.6302, p=0.0003, effect=0.2654
- Youden 最优阈值: threshold=56.0, balanced acc=0.6166 (TPR=0.8903, TNR=0.3429)

## 3. 逐维度 point-biserial (cluster-bootstrap CI 聚类于 instance)
| 维度 | r | CI95 |
|---|---|---|
| A 任务理解 | 0.2363 | [0.0838, 0.3646] |
| B 根因定位 | 0.2192 | [0.0834, 0.3525] |
| C 实现正确性 | 0.2438 | [0.1192, 0.3605] |
| D 测试证据 | 0.2416 | [0.1237, 0.3688] |
| E 执行纪律 | 0.2137 | [0.0878, 0.335] |
| F 收尾卫生 | 0.2403 | [0.1324, 0.327] |

## 4. 维度分解:过程信号 vs outcome 同义
> 若 process_only(A+B+E) 的 AUC 明显低于 outcome(C+D+F)/full,说明相关主要来自与 resolved 同义的 caps;
> 若 A+B+E 仍有实质性 AUC,说明 rubric 确实贡献了独立的过程判别信号。

| predictor | AUC-ROC | CI95 |
|---|---|---|
| process_only_AB_E | 0.6214 | [0.5409, 0.7062] |
| outcome_CDF | 0.6343 | [0.5631, 0.7085] |
| full_total | 0.6327 | [0.5618, 0.7091] |

## Limitations
- **Outcome-entangled caps**: the rubric's caps (empty/unappliable patch -> total<=49, persisting runtime errors -> <=59, no post-fix verification -> <=74) partly overlap the definition of NOT-resolved, so C/D/F (and total) correlate with resolved partly by construction. The A+B+E vs C+D+F AUC split quantifies the rubric's added signal beyond outcome.
- **Judge self-family bias**: Codex (gpt-5 family) judges gpt-5.2/kimi/gemini trajectories; possible in-group favoritism. Re-judge a subset with a different model or humans to bound it.
- **Selection bias**: all 11 joinable submissions use the mini-swe-agent harness and skew resolved-high (~0.66); AUC ceiling is limited. Population = mini-swe-agent family runs on SWE-bench Verified.
- **Epistemic circularity**: this rubric was derived from LLM analysis of trajectories; validating with an LLM judge is partly circular. Triangulate with a human spot-check of 30-50 cases (human vs codex, human vs resolved).
- **Per-instance label trust**: compare_verified.json already drops the 0/500 gemini-pro submission; if any other submission's per_instance_details.json is similarly poisoned, downstream labels are wrong.
