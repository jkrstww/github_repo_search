# Refact.ai Agent

**Refact.ai is an open-source AI Agent for IDEs that handles real software engineering tasks end-to-end: plans, executes, tests, self-corrects, and delivers a production-ready solution fully autonomously.**

Unlike suggestion-based tools, Refact.ai Agent independently forms its own strategy and iterates until the task is complete, while allowing users to preview and control the process when needed.

Our goal: empower every developer with an Autonomous AI Agent that amplifies their capabilities and helps them achieve 10x more — programming alongside a digital twin they can trust.

### Key features

- **Truly autonomous programming:** solves tasks from start to finish.
- **Full-repo context awareness:** understands and navigates complex, real-world codebases.
- **Dynamic tool use:** Chooses and applies tools to explore, modify, think, and test as needed.
- **Access to SOTA models**: Claude 3.7 Sonnet, o4-mini, and more.
- **Dev tools integration**: Connects with your GitHub, PostgreSQL, Docker, and more to mimic your workflow.
- **Optional on-prem deployment** for full data control.

## SWE-Bench Lite Score

Refact.ai Agent achieved a SOTA score of 59,7% on SWE-Bench Lite, solving 179 out of 300 tasks. This result puts Refact.ai at the top of the leaderboard and makes it the best-performing open-source AI Agent on the benchmark to date.
| Total | Solved | Not solved | Solved (%) | Unresolved (%) |
| ------ | ---------| ---------- | -----------| -------------- |
| 300 | 179 | 121 | 59,7% | 40,3% |

## Evaluation Results

astropy/astropy: 2/6 (33.3%)

django/django: 78/114 (68.4%)

matplotlib/matplotlib: 11/23 (47.8%)

mwaskom/seaborn: 2/4 (50.0%)

pallets/flask: 0/3 (0.0%)

psf/requests: 5/6 (83.3%)

pydata/xarray: 2/5 (40.0%)

pylint-dev/pylint: 3/6 (50.0%)

pytest-dev/pytest: 10/17 (58.8%)

scikit-learn/scikit-learn: 17/23 (73.9%)

sphinx-doc/sphinx: 6/16 (37.5%)

sympy/sympy: 43/77 (55.8%)

_Notably, Refact.ai solved 2 SWE-bench tasks that no other listed agents have (django-12589, sympy-21627)._

## Submission checklist

- [x] Is a pass@1 submission (does not attempt the same task instance more than once)
- [x] Does not use SWE-bench test knowledge (`PASS_TO_PASS`, `FAIL_TO_PASS`)
- [x] Does not use the `hints` field in SWE-bench
- [x] Does not have web-browsing OR has taken steps to prevent lookup of SWE-bench solutions via web-browsing

## Want to see Refact.ai Agent in Action?

- Visit our [website](https://refact.ai/)
- Explore [our Github](https://github.com/smallcloudai/refact)
- Get for [VS Code](https://marketplace.visualstudio.com/items?itemName=smallcloud.codify) or [JetBrains](https://plugins.jetbrains.com/plugin/20647-refact--open-source-ai-agent-code-generator--chat).
