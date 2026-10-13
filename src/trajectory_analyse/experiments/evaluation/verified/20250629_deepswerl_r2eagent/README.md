## DeepSWE-Preview

We introduce **`DeepSWE-Preview`**, a reasoning-enabled coding agent trained from scratch from `Qwen3-32B` with only reinforcement learning (RL). It achieves **59.2%** on SWE-Bench-Verified with test-time scaling, reaching SOTA for open-weight coding agents  (**42.2%** Pass@1, **71.0%** Pass@16).

DeepSWE is trained using [**rLLM**](https://www.notion.so/21b81902c146819db63cd98a54ba5f31?pvs=21), our framework for post-training language agents using [**R2E-Gym**](https://r2e-gym.github.io/) environments. We’ve **open sourced** everything—our dataset, code, training, and evaluation logs, for everyone to progress on scaling and improving agents with RL.


## Performance

```md
Submission summary for 20250629_deepswerl_r2eagent on SWE-bench verified split
==================================================
Resolved 211 instances (42.2%)
==================================================
Resolved by Repository
- astropy/astropy: 5/22 (22.73%)
- django/django: 103/231 (44.59%)
- matplotlib/matplotlib: 11/34 (32.35%)
- mwaskom/seaborn: 0/2 (0.0%)
- pallets/flask: 1/1 (100.0%)
- psf/requests: 3/8 (37.5%)
- pydata/xarray: 11/22 (50.0%)
- pylint-dev/pylint: 2/10 (20.0%)
- pytest-dev/pytest: 11/19 (57.89%)
- scikit-learn/scikit-learn: 21/32 (65.62%)
- sphinx-doc/sphinx: 15/44 (34.09%)
- sympy/sympy: 28/75 (37.33%)
==================================================
Resolved by Time
- 2013: 2/3 (66.67%)
- 2014: 1/2 (50.0%)
- 2015: 0/1 (0.0%)
- 2016: 0/2 (0.0%)
- 2017: 9/16 (56.25%)
- 2018: 9/24 (37.5%)
- 2019: 50/98 (51.02%)
- 2020: 43/108 (39.81%)
- 2021: 30/86 (34.88%)
- 2022: 42/102 (41.18%)
- 2023: 25/58 (43.1%)
```

# Details
Commit to reproduce in rLLM: [TODO]


# Checklist

- [X] Is a pass@1 submission (does not attempt the same task instance more than once)
- [X] Does not use SWE-bench test knowledge (`PASS_TO_PASS`, `FAIL_TO_PASS`)
- [X] Does not use the `hints` field in SWE-bench
- [X] Does not have web-browsing OR has taken steps to prevent lookup of SWE-bench solutions via web-browsing

---


If you found this work helpful, please consider citing it using the following:
```
@misc{deepswe2025,
  title={DeepSWE: Training a State-of-the-Art Coding Agent from Scratch by Scaling RL},
  author={Michael Luo, Naman Jain, Jaskirat Singh, Sijun Tan, Ameen Patel, Qingyang Wu, Alpay Ariyak, Colin Cai, Tarun Venkat, Manan Roongta, Li Erran Li, Raluca Ada Popa, Koushik Sen, Ion Stoica},
  howpublished={\url{N/A}},
  note={Notion Blog},
  year={2025}
}
```

