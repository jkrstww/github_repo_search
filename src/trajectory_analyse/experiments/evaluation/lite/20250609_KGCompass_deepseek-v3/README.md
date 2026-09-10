# KGCompass: Knowledge Graph Enhanced Repository-Level Software Repair

[![Code](https://img.shields.io/badge/Code-KGCompass-blue)](https://github.com/GLEAM-Lab/KGCompass)
[![Model](https://img.shields.io/badge/Model-DeepSeek--V3-green)](https://huggingface.co/deepseek-ai/DeepSeek-V3)
[![Paper](https://img.shields.io/badge/Paper-arXiv-red)](https://arxiv.org/abs/2503.21710)

KGCompass is a novel approach for repository-level software repair that accurately links code structure with repository metadata using a knowledge graph, enabling more precise bug localization and patch generation.

![KGCompass](https://gcdnb.pbrd.co/images/pXnwAe3e5YlQ.png?o=1)

## Verification

The results have been verified using the `sb-cli` tool and are consistent with the results reported below.

- **Submission ID**: `kgcompass`
- **Submitter Email**: `yby@ieee.org`

## Result

```
Submission summary for 20250609_KGCompass_deepseek-v3 on SWE-bench lite split
==================================================
Resolved 110 instances (36.67%)
==================================================
Resolved by Repository
- astropy/astropy: 3/6 (50.0%)
- django/django: 52/114 (45.61%)
- matplotlib/matplotlib: 5/23 (21.74%)
- mwaskom/seaborn: 2/4 (50.0%)
- pallets/flask: 1/3 (33.33%)
- psf/requests: 3/6 (50.0%)
- pydata/xarray: 1/5 (20.0%)
- pylint-dev/pylint: 1/6 (16.67%)
- pytest-dev/pytest: 8/17 (47.06%)
- scikit-learn/scikit-learn: 10/23 (43.48%)
- sphinx-doc/sphinx: 1/16 (6.25%)
- sympy/sympy: 23/77 (29.87%)
==================================================
Resolved by Time
- 2012: 0/1 (0.0%)
- 2014: 1/3 (33.33%)
- 2015: 1/1 (100.0%)
- 2016: 1/4 (25.0%)
- 2017: 5/16 (31.25%)
- 2018: 5/21 (23.81%)
- 2019: 22/59 (37.29%)
- 2020: 20/66 (30.3%)
- 2021: 21/42 (50.0%)
- 2022: 23/57 (40.35%)
- 2023: 11/30 (36.67%)
```

Please copy paste this checklist in your `README.md` and confirm the following:
- [x] Is a pass@1 submission (does not attempt the same task instance more than once)
- [x] Does not use SWE-bench test knowledge (`PASS_TO_PASS`, `FAIL_TO_PASS`)
- [x] Does not use the `hints` field in SWE-bench
- [x] Does not have web-browsing OR has taken steps to prevent lookup of SWE-bench solutions via web-browsing