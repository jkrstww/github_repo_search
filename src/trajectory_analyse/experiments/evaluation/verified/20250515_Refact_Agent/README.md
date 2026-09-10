# [Refact.ai](http://refact.ai/) Agent

**[Refact.ai](http://refact.ai/) is an open-source AI Agent for IDEs that handles real software engineering tasks end-to-end.** It plans, executes, tests, self-corrects, and delivers a production-ready solutions fully autonomously — while allowing users to preview and control the process when needed.

[Our SWE-bench pipeline is open-sourced on GitHub](https://github.com/smallcloudai/refact-bench). You can implement the same components and run the benchmark just like we did — to reproduce Refact.ai Agent approach and score end-to-end.

### Key features

- Solves tasks autonomously from start to finish
- Full-repo context awareness
- SOTA models & dev tools integration
- Optional on-prem deployment.

## SWE-Bench Verified Score

[Refact.ai](http://refact.ai/) Agent achieved a 69,8% score, solving 349 out of 500 tasks. This makes [Refact.ai](http://refact.ai/) SOTA on SWE-Bench Verified and the best-performing source AI Agent in the leaderboard to date.
| Total | Solved | Not solved | Solved (%) | Unresolved (%) |
| ------ | ---------| ---------- | -----------| -------------- |
| 500 | 349 | 151 | 69,8% | 30,2% |

## Submission summary for 20250515_Refact_Agent on SWE-bench verified split

Resolved 349 instances (69.8%).
Resolved by Repository

- astropy/astropy: 9/22 (40.91%)
- django/django: 165/231 (71.43%)
- matplotlib/matplotlib: 20/34 (58.82%)
- mwaskom/seaborn: 0/2 (0.0%)
- pallets/flask: 1/1 (100.0%)
- psf/requests: 6/8 (75.0%)
- pydata/xarray: 18/22 (81.82%)
- pylint-dev/pylint: 4/10 (40.0%)
- pytest-dev/pytest: 16/19 (84.21%)
- scikit-learn/scikit-learn: 28/32 (87.5%)
- sphinx-doc/sphinx: 28/44 (63.64%)
- sympy/sympy: 54/75 (72.0%)

---

Resolved by Time

- 2013: 3/3 (100.0%)
- 2014: 2/2 (100.0%)
- 2015: 0/1 (0.0%)
- 2016: 2/2 (100.0%)
- 2017: 13/16 (81.25%)
- 2018: 14/24 (58.33%)
- 2019: 73/98 (74.49%)
- 2020: 79/108 (73.15%)
- 2021: 54/86 (62.79%)
- 2022: 71/102 (69.61%)
- 2023: 38/58 (65.52%)

## Submission checklist

- [x] Is a pass@1 submission (does not attempt the same task instance more than once)
- [x] Does not use SWE-bench test knowledge (`PASS_TO_PASS`, `FAIL_TO_PASS`)
- [x] Does not use the `hints` field in SWE-bench
- [x] Does not have web-browsing OR has taken steps to prevent lookup of SWE-bench solutions via web-browsing

## To explore [Refact.ai Agent](http://refact.ai/)

- [Website](https://refact.ai/)
- [Github](https://github.com/smallcloudai/refact)
- Get for [VS Code](https://marketplace.visualstudio.com/items?itemName=smallcloud.codify) or [JetBrains](https://plugins.jetbrains.com/plugin/20647-refact--open-source-ai-agent-code-generator--chat).
