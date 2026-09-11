# ExpeRepair-v1.0 + Claude 4 Sonnet

## Results

Submission summary for 20250625_ExpeRepair-v1.0 + Claude 4 Sonnet on SWE-bench lite split
==================================================
Resolved 181 instances (60.33%)
==================================================
Resolved by Repository
- astropy/astropy: 3/6 (50.0%)
- django/django: 79/114 (69.3%)
- matplotlib/matplotlib: 12/23 (52.17%)
- mwaskom/seaborn: 2/4 (50.0%)
- pallets/flask: 0/3 (0.0%)
- psf/requests: 3/6 (50.0%)
- pydata/xarray: 2/5 (40.0%)
- pylint-dev/pylint: 4/6 (66.67%)
- pytest-dev/pytest: 10/17 (58.82%)
- scikit-learn/scikit-learn: 16/23 (69.57%)
- sphinx-doc/sphinx: 9/16 (56.25%)
- sympy/sympy: 41/77 (53.25%)
==================================================
Resolved by Time
- 2012: 1/1 (100.0%)
- 2014: 1/3 (33.33%)
- 2015: 0/1 (0.0%)
- 2016: 1/4 (25.0%)
- 2017: 9/16 (56.25%)
- 2018: 11/21 (52.38%)
- 2019: 41/59 (69.49%)
- 2020: 35/66 (53.03%)
- 2021: 29/42 (69.05%)
- 2022: 36/57 (63.16%)
- 2023: 17/30 (56.67%)



## Submission Checklist

- [x] Is a pass@1 submission (does not attempt the same task instance more than once)
- [x] Does not use SWE-bench test knowledge (`PASS_TO_PASS`, `FAIL_TO_PASS`)
- [x] Does not use the `hints` field in SWE-bench
- [x] Does not have web-browsing OR has taken steps to prevent lookup of SWE-bench solutions via web-browsing

## Introduction

ExpeRepair is a novel LLM-based program repair approach that continuously accumulates and reuses historical repair experiences through a dual-memory system. It is built upon two key components: the Program Repair Module and the Memory Module. The Program Repair Module consists of a Test Agent and a Patch Agent, which collaboratively perform three essential tasks: test generation, patch generation, and patch validation. After addressing an issue, the Memory Module captures the corresponding repair trajectory, extracting concrete demonstrations and summarizing high-level repair insights. These are stored in episodic memory and semantic memory, respectively. During subsequent repairs, ExpeRepair dynamically retrieves relevant demonstrations and insights to inform and enhance its repair strategies for new issues.

You can learn more about ExpeRepair in our paper: [ExpeRepair arXiv paper](https://arxiv.org/abs/2506.10484).


The results in this submission are produced by ExpeRepair-v1.0, a lightweight version of ExpeRepair, with the following differences:
1. Memory Accumulation: ExpeRepair-v1.0 adopts a simpler way — it attempts to reproduce all issues, and if reproduction is successful (as determined by the LLM), it completes the entire issue resolution process. The repair trajectories from this process are then saved and organized in the Memory Module.
2. Memory Usage: ExpeRepair-v1.0 retrieves only demonstrations from the Memory Module, without utilizing summarized insights, and uses the top-k retrieved demonstrations to improve both reproduction script generation and patch generation.
3. Model Usage: For reproduction, localization, and patch generation, ExpeRepair-v1.0 uses Claude-4-Sonnet for code generation, employs o4-mini for reasoning, and replaces DeepSeek-R1 with o4-mini for patch validation.


## Trajectories

We have included the trajectory files for each phase:

1. reproduction:
- conv_reproduce_X.json: the X-th attempt at generating a reproduction script.
- conv_reproduce_correct_X.json: LLM's determination of whether the X-th generated script is correct.
- conv_3_verified_test.json: in the validation stage, we additionally generate 3 test scripts for each issue.
- reproduce_experiences.jsonl: records the trajectories of the reproduction phase.

2. localization:
- search/search_round_file_[initial/refine].json: initially selects multiple suspicious files, then refines the selection to up to four files.
- search/search_round_edit_initial_X.json: identifies bug locations in the X-th selected file.
- search/search_round_edit_refine.json: combines all identified bug locations and selects the most likely ones.

3. generation:
- conv_patch_X.json: generates several candidate patches given the issue and retrieved demonstrations.
- conv_refine_patch_X.json: if none of the patches pass the reproduction script, refines them based on feedback (execution results).
- conv_expand_patch_X.json: if at least one patch passes, further improves the patch, since it may be a false positive due to incomplete tests.
- final_patch_X.json: saves all generated patches during the generation process.
- patch_experiences.jsonl: records the trajectories of the patch generation phase.

4. validation:
- select_patch_initial.json (optional): initially selects several patches based on their execution results on the reproduction script.
- select_patch_second.jsonl: ranks the selected patches based on their execution results on the 3 additional test scripts.
- ranked_final_patch.jsonl: final ranking of all generated patches. We submit only the top-ranked patch and submit only once.

