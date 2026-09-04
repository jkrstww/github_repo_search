# SWE-bench-Verified 轨迹分析
  - SWE-bench_Verified 数据集本身只包含 500 个评测任务，不包含 agent 的执行轨迹。
  - 轨迹按提交/模型分散保存，通常位于官方匿名 S3，较新的提交位于提交者公开 GitHub 仓库。官方说明见 SWE-bench/experiments
    README (https://github.com/SWE-bench/experiments)。

  最方便的方式是使用官方仓库脚本：

  git clone --depth 1 https://github.com/SWE-bench/experiments.git
  cd experiments
  pip install boto3 pyyaml

  # 下载某个 Verified 提交的轨迹
  python -m analysis.download_logs \
    evaluation/verified/20241120_artemis_agent \
    --only_trajs

  # 下载全部 Verified 提交的轨迹
  python -m analysis.download_logs \
    evaluation/verified \
    --only_trajs \
    --skip_existing

  轨迹会放到对应提交目录的 trajs/ 下，格式不统一，例如：

  - .traj
  - .txt
  - .json
  - .traj.json

  也可以直接从 S3 下载单个文件：

  curl -L \
    'https://swe-bench-submissions.s3.amazonaws.com/verified/20241120_artemis_agent/trajs/astropy__astropy-12907.txt' \
    -o astropy__astropy-12907.txt

  S3 目录可以匿名列出：

  curl -L \
    'https://swe-bench-submissions.s3.amazonaws.com/?list-type=2&prefix=verified/20241120_artemis_agent/trajs/'

  较新的提交会在 metadata.yaml 的 assets.repo 中指向公开 artifact 仓库，例如：

  assets:
    repo: https://github.com/john-b-yang/20260901_mini-v2.4.2_gemini-3-5-flash
    trajs: .../tree/main/trajs

  然后直接：

  git clone --depth 1 \
    https://github.com/john-b-yang/20260901_mini-v2.4.2_gemini-3-5-flash

  注意：

  1. 并非每个提交都保证轨迹完整，脚本会提示缺失的 trajs/。
  2. trajs_docent 一类链接通常是在线浏览入口，不一定提供批量原始文件下载。
  3. 同一提交可能包含 logs/、patch.diff、测试输出和轨迹；轨迹内容和字段取决于 agent 实现。
  4. 当前官方仓库还提供了 swebench submit verify，可以基于公开测试输出重新核验结果，无需重新执行 Docker。

  ## 按年份批量下载

  `download_trajs.py` 会扫描 `experiments/evaluation/` 下各 split 的提交目录，
  默认下载目录名以 `2026` 开头的提交。目标目录已存在 `trajs/` 时会跳过，即使该目录为空：

  ```bash
  python download_trajs.py
  python download_trajs.py --year 2025
  ```

  ## 找出结果不一致的轨迹

  `diff_trajectories.py` 会读取指定数据集下、指定年份提交目录中的
  `per_instance_details.json`，找出同一 sample 的 `resolved` 值不一致的提交，
  并复制对应的 Harbor 轨迹（`trajs_harbor/<sample>/trajectory.json`）：

  ```bash
  python diff_trajectories.py
  python diff_trajectories.py --year 2026 --dataset verified \
    --output_dir experiments/diff_trajectories/verified
  python diff_trajectories.py --diff_num 3
  ```

  默认输出目录为 `experiments/diff_trajectories/<dataset>/`。每个差异 sample
  有一个同名目录，轨迹按 `<submission>.json` 保存；根目录的 `compare.json`
  记录这些文件对应的 `resolved` 值。`--diff_num N` 只保留 true/false
  两组中数量较少的一组至少为 `N` 的 sample；每次运行会清理该输出目录中的
  上一次生成结果。只有存在且非空的 `trajs_harbor/` 提交目录才会参与比较。

  ## 使用 Codex 分析轨迹

  `analyse_trajectories.py` 接收一个 sample 目录和前一步生成的
  `compare.json`，调用本机 `codex exec` 阅读轨迹并生成中文分析报告：

  ```bash
  python analyse_trajectories.py \
    --trajectories_dir experiments/diff_trajectories/verified/sympy__sympy-20916 \
    --trajectories_lable experiments/diff_trajectories/verified/compare.json
  ```

  报告写入输入目录下的 `analyse.md`，内容包括成功/失败原因、100 分制轨迹
  评测细则、设计依据、具体案例和改进建议。Codex 以只读沙箱运行，不会修改
  轨迹文件；`--timeout` 可调整单次分析的超时时间。

  批量分析全部差异 sample：

  ```bash
  python batch_analyse_trajectories.py
  ```

  批处理脚本默认扫描 `experiments/diff_trajectories/verified/`，使用同目录下的
  `compare.json`，并在每个 sample 目录生成 `analyse.md`。单个 sample 失败时会
  继续处理其余目录，进程结束时汇总失败项；可用 `--timeout` 调整每条轨迹的分析
  超时时间。
