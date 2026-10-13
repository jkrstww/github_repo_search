## DeepSWE-Preview

We introduce **`DeepSWE-Preview`**, a reasoning-enabled coding agent trained from scratch from `Qwen3-32B` with only reinforcement learning (RL). It achieves **59.2%** on SWE-Bench-Verified with test-time scaling, reaching SOTA for open-weight coding agents  (**42.2%** Pass@1, **71.0%** Pass@16).

DeepSWE is trained using [**rLLM**](https://www.notion.so/21b81902c146819db63cd98a54ba5f31?pvs=21), our framework for post-training language agents using [**R2E-Gym**](https://r2e-gym.github.io/) environments. We’ve **open sourced** everything—our dataset, code, training, and evaluation logs, for everyone to progress on scaling and improving agents with RL.

## Performance

```md
Submission summary for 20250629_deepswerl_r2eagent_tts on SWE-bench verified split
==================================================
Resolved 294 instances (58.8%)
==================================================
Resolved by Repository
- astropy/astropy: 11/22 (50.0%)
- django/django: 134/231 (58.01%)
- matplotlib/matplotlib: 21/34 (61.76%)
- mwaskom/seaborn: 0/2 (0.0%)
- pallets/flask: 1/1 (100.0%)
- psf/requests: 4/8 (50.0%)
- pydata/xarray: 17/22 (77.27%)
- pylint-dev/pylint: 2/10 (20.0%)
- pytest-dev/pytest: 13/19 (68.42%)
- scikit-learn/scikit-learn: 24/32 (75.0%)
- sphinx-doc/sphinx: 22/44 (50.0%)
- sympy/sympy: 45/75 (60.0%)
==================================================
Resolved by Time
- 2013: 3/3 (100.0%)
- 2014: 1/2 (50.0%)
- 2015: 0/1 (0.0%)
- 2016: 1/2 (50.0%)
- 2017: 11/16 (68.75%)
- 2018: 14/24 (58.33%)
- 2019: 63/98 (64.29%)
- 2020: 64/108 (59.26%)
- 2021: 40/86 (46.51%)
- 2022: 60/102 (58.82%)
- 2023: 37/58 (63.79%)
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