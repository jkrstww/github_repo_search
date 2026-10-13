# Lingxi

[Lingxi](https://github.com/nimasteryang/Lingxi) is an open‑source, multi‑agent framework designed to automate a broad range of software‑engineering tasks. Lingxi is meant to be a playground for anyone who wants to try automating software‑engineering.

The submission is our initial implementation on SWE-Bench on Lingxi. For more detailed, please visit our Github repo or see our [Technical Report](https://github.com/nimasteryang/Lingxi/blob/master/docs/Lingxi%20Technical%20Report%202505.pdf) which detailed how we tackle SWE-Bench on Lingxi.


## Results
```
==================================================
Resolved 128 instances (42.67%)
==================================================
Resolved by Repository
- astropy/astropy: 2/6 (33.33%)
- django/django: 63/114 (55.26%)
- matplotlib/matplotlib: 10/23 (43.48%)
- mwaskom/seaborn: 2/4 (50.0%)
- pallets/flask: 0/3 (0.0%)
- psf/requests: 1/6 (16.67%)
- pydata/xarray: 1/5 (20.0%)
- pylint-dev/pylint: 4/6 (66.67%)
- pytest-dev/pytest: 7/17 (41.18%)
- scikit-learn/scikit-learn: 12/23 (52.17%)
- sphinx-doc/sphinx: 0/16 (0.0%)
- sympy/sympy: 26/77 (33.77%)
==================================================
Resolved by Time
- 2012: 0/1 (0.0%)
- 2014: 0/3 (0.0%)
- 2015: 0/1 (0.0%)
- 2016: 1/4 (25.0%)
- 2017: 5/16 (31.25%)
- 2018: 6/21 (28.57%)
- 2019: 29/59 (49.15%)
- 2020: 26/66 (39.39%)
- 2021: 19/42 (45.24%)
- 2022: 29/57 (50.88%)
- 2023: 13/30 (43.33%)
```
## Checklist

- [x] Is a pass@1 submission (does not attempt the same task instance more than once)
- [x] Does not use SWE-bench test knowledge (`PASS_TO_PASS`, `FAIL_TO_PASS`)
- [x] Does not use the `hints` field in SWE-bench
- [x] Does not have web-browsing OR has taken steps to prevent lookup of SWE-bench solutions via web-browsing
