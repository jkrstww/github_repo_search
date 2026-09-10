# 轨迹打分 vs resolved 相关性报告
- 可打分 pair 数: 966; resolved True/False = 568/398; base rate = 0.588
- 抽样单元: 非独立的 (instance × model) 对; 主信号=实例内配对 Δ(cluster-bootstrap CI 聚类于 instance)

## 1. 主信号:实例内配对 (primary)
- 在 100 个同时含 True/False 的 instance 内计算 Δ = mean(分|True) - mean(分|False)
- Δ 均值 = 8.9479; Δ>0 的 instance 比例 = 0.74
- 符号检验: +74/-26/0×0 (n=100, p=1.667362649450101e-06)
- Wilcoxon signed-rank: W+=4231.0, z=5.864093350872419, p=4.51593384909188e-09
> 同题内消掉实例难度后才回答「分高的 run 更可能是 resolved 的吗」。

## 2. 全局判别力 (cluster-bootstrap CI 聚类于 instance)
- AUC-ROC = 0.6352  CI95 = [0.5945, 0.6738]
- PR-AUC = 0.6718  (base rate = 0.588)
- point-biserial r = 0.2525  CI95 = [0.1832, 0.326]
- 分组均值: True=77.9859, False=69.7312
- Mann-Whitney: U=143593.0, z=7.1623, p=0.0, effect=0.2704
- Youden 最优阈值: threshold=80.5, balanced acc=0.6087 (TPR=0.544, TNR=0.6734)

## 3. 逐维度 point-biserial (cluster-bootstrap CI 聚类于 instance)
| 维度 | r | CI95 |
|---|---|---|
| A 任务理解 | 0.1985 | [0.1382, 0.2576] |
| B 根因定位 | 0.2215 | [0.151, 0.2894] |
| C 实现正确性 | 0.2403 | [0.1689, 0.3113] |
| D 测试证据 | 0.2365 | [0.1642, 0.3109] |
| E 执行纪律 | 0.2039 | [0.1334, 0.2746] |
| F 收尾卫生 | 0.232 | [0.1644, 0.2955] |

## 4. 维度分解:过程信号 vs outcome 同义
> 若 process_only(A+B+E) 的 AUC 明显低于 outcome(C+D+F)/full,说明相关主要来自与 resolved 同义的 caps;
> 若 A+B+E 仍有实质性 AUC,说明 rubric 确实贡献了独立的过程判别信号。

| predictor | AUC-ROC | CI95 |
|---|---|---|
| process_only_AB_E | 0.6215 | [0.585, 0.661] |
| outcome_CDF | 0.6368 | [0.5962, 0.6802] |
| full_total | 0.6352 | [0.5948, 0.6777] |

## Limitations
- **Outcome-entangled caps**: the rubric's caps (empty/unappliable patch -> total<=49, persisting runtime errors -> <=59, no post-fix verification -> <=74) partly overlap the definition of NOT-resolved, so C/D/F (and total) correlate with resolved partly by construction. The A+B+E vs C+D+F AUC split quantifies the rubric's added signal beyond outcome.
- **Judge self-family bias**: Codex (gpt-5 family) judges gpt-5.2/kimi/gemini trajectories; possible in-group favoritism. Re-judge a subset with a different model or humans to bound it.
- **Selection bias**: all 11 joinable submissions use the mini-swe-agent harness and skew resolved-high (~0.66); AUC ceiling is limited. Population = mini-swe-agent family runs on SWE-bench Verified.
- **Epistemic circularity**: this rubric was derived from LLM analysis of trajectories; validating with an LLM judge is partly circular. Triangulate with a human spot-check of 30-50 cases (human vs codex, human vs resolved).
- **Per-instance label trust**: compare_verified.json already drops the 0/500 gemini-pro submission; if any other submission's per_instance_details.json is similarly poisoned, downstream labels are wrong.
