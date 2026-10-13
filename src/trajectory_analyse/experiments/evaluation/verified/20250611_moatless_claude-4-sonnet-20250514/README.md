# Moatless Tools
Moatless Tools is a hobby project where I experiment with some ideas I have about how LLMs can be used to edit code in large existing codebases.

Top changes:

- **Claude 4.0 Sonnet**
  Now running Claude 4.0 Sonnet, which delivers significantly higher results but requires more iterations per trajectory, increasing the cost per instance to $0.64

- **New tools**
To fit into Claude 4.0 Sonnet's troubleshooting flow, which seems to involve constantly creating and running debug files, and since I’m a bit hesitant to use a general bash tool, I added a tool for creating and running Python scripts. I also added an improved grep tool for general code searching.

- **Unified Docker Container**
  The testbed and agent now run in the same Docker container for streamlined test execution and evaluation.

## For more information
- [Moatless Tools on GitHub](https://github.com/aorwall/moatless-tools)

## Checklist

- [x] Is a pass@1 submission (does not attempt the same task instance more than once)
- [x] Does not use SWE-bench test knowledge (`PASS_TO_PASS`, `FAIL_TO_PASS`)
- [x] Does not use the `hints` field in SWE-bench
- [x] Does not have web-browsing OR has taken steps to prevent lookup of SWE-bench solutions via web-browsing

## Submission summary
```
Submission summary for 20250611_moatless_claude-4-sonnet-20250514 on SWE-bench verified split
==================================================
Resolved 354 instances (70.8%)
==================================================
Resolved by Repository
- astropy/astropy: 11/22 (50.0%)
- django/django: 172/231 (74.46%)
- matplotlib/matplotlib: 23/34 (67.65%)
- mwaskom/seaborn: 1/2 (50.0%)
- pallets/flask: 1/1 (100.0%)
- psf/requests: 5/8 (62.5%)
- pydata/xarray: 16/22 (72.73%)
- pylint-dev/pylint: 6/10 (60.0%)
- pytest-dev/pytest: 15/19 (78.95%)
- scikit-learn/scikit-learn: 26/32 (81.25%)
- sphinx-doc/sphinx: 27/44 (61.36%)
- sympy/sympy: 51/75 (68.0%)
==================================================
Resolved by Time
- 2013: 3/3 (100.0%)
- 2014: 2/2 (100.0%)
- 2015: 0/1 (0.0%)
- 2016: 2/2 (100.0%)
- 2017: 12/16 (75.0%)
- 2018: 18/24 (75.0%)
- 2019: 73/98 (74.49%)
- 2020: 80/108 (74.07%)
- 2021: 53/86 (61.63%)
- 2022: 69/102 (67.65%)
- 2023: 42/58 (72.41%)
```