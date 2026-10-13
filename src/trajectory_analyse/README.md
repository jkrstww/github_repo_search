# SWE-bench-Verified 轨迹分析
  - SWE-bench_Verified 数据集本身只包含 500 个评测任务，不包含 agent 的执行轨迹
  - 轨迹按提交/模型分散保存，通常位于官方匿名 S3，较新的提交位于提交者公开 GitHub 仓库。官方说明见 SWE-bench/experiments
    README (https://github.com/SWE-bench/experiments)

## 轨迹下载
最方便的方式是使用官方仓库脚本：
```bash
git clone --depth 1 https://github.com/SWE-bench/experiments.git
cd experiments
pip install boto3 pyyaml
```

下载某个 Verified 提交的轨迹
```bash
python -m analysis.download_logs \
  evaluation/verified/20241120_artemis_agent \
  --only_trajs
```

下载全部 Verified 提交的轨迹
```bash
python -m analysis.download_logs \
  evaluation/verified \
  --only_trajs \
  --skip_existing
```

按年份批量下载
```bash
python download_trajs.py
python download_trajs.py --year 2025
```

## rubric 蒸馏
找出结果不一致的轨迹，以便后续分析不同轨迹之间的差异性
```bash
python diff_trajectories.py
python diff_trajectories.py --year 2026 --dataset verified \
  --output_dir experiments/diff_trajectories/verified
python diff_trajectories.py --diff_num 3
```

默认输出目录为 `experiments/diff_trajectories/<dataset>/`，
根目录的 `compare.json`记录这些文件对应的 `resolved` 值，
`--diff_num N` 只保留 true/false 两组中数量较少的一组至少为 `N` 的 sample

## 使用 Codex 分析轨迹

调用本机 `codex exec` 阅读轨迹并生成中文分析报告：

```bash
python analyse_trajectories.py \
  --trajectories_dir experiments/diff_trajectories/verified/sympy__sympy-20916 \
  --trajectories_lable experiments/diff_trajectories/verified/compare.json
```

报告写入输入目录下的 `analyse.md`，内容包括成功/失败原因、100 分制轨迹评测细则、设计依据、具体案例和改进建议

批量分析全部差异 sample：

```bash
python batch_analyse_trajectories.py
```

合并 analyse.md 得到 rubric

# rubric self-evolving

单条轨迹量化评分

```bash
python score_trajectories.py path/to/trajectory.json --model model --output-dir path/to/scores
```

# rubric 自动迭代

## `iterate_score_rules.py`（score_rules 模板）
针对 rubric/base_rules.yaml 进行迭代，仅修改子维度中的 score_rules

```bash
python iterate_score_rules.py \
  --train_dataset  path/to/train_job/trajs_harbor \
  --validate_dataset path/to/validate_job/trajs_harbor \
  --rollout_num 5 \
  --model theta-judge \
  --critic-model theta-critic \
  --seed 42
```

## `iterate_base_rubric` (base_0 模板)
自动化迭代 iterated_rubric/base_0.yaml v2，基于 base_0.yaml 进行迭代

编辑协议在 iterate_score_rules 的规则级 add/delete/modify 之上扩展了 subcriteria 级：
* add_subcriterion: 新行为 → 增指标
* delete_subcriterion: reasoning 不清晰 → 改 score_rules
* modify_subcriterion: 历史证实的反复极低分特例 → 删指标

```bash
python iterate_base_rubric.py \
    --train_dataset <verified-job> --verified_dataset <verified-job> \
    --train_num 30 --iteration_num 3 --model <judge> [--critic-model <m>] \
    [--rubric trajectory_scoring_skill/iterated_rubric/base_0.yaml] \
    [--seed 42] [--output-dir <dir>]
```
