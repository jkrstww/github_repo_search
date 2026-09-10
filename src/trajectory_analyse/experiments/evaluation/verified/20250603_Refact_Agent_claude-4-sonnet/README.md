# [Refact.ai Agent](http://refact.ai/)

**[Refact.ai](http://refact.ai/) is an open-source AI Agent for IDEs that handles real software engineering tasks end-to-end.** It plans, executes, tests, self-corrects, and delivers production-ready solutions fully autonomously — while allowing users to preview and control the process when needed.

### Key features

- Solves tasks autonomously, from start to finish
- Fully understands your codebase, not just open tabs
- Transparent by design — every step is visible and reversible
- Integrates with dev tools (GitHub, pdb, Web, MCP, and more) to complete tasks across systems
- BYOK-friendly or self-hosted if you want full control.

## SWE-Bench Verified Score with Claude 4 Sonnet

[Refact.ai](http://refact.ai/) Agent achieved a 74.4% score, solving 372 out of 500 tasks. This makes Refact.ai #1 AI Agent in SWE-bench Verified and the top-ranked open-source programming agent.
At the core of this run is Anthropic’s Claude 4 Sonnet, which provided a significant boost in reasoning and coding capability. The new score surpasses our previous best — 70.4% with Claude 3.7 Sonnet.
| Total | Solved | Not solved | Solved (%) | Unresolved (%) |
| ------ | ---------| ---------- | -----------| -------------- |
| 500 | 372 | 128 | 74.4% | 25.6% |
[Our SWE-bench pipeline is open-sourced on GitHub](https://github.com/smallcloudai/refact-bench) and reproducible end-to-end.
[Read the blog](https://refact.ai/blog/2025/1-agent-on-swe-bench-verified-using-claude-4-sonnet) for a technical breakdown.

## Submission summary

Resolved 372 instances (74.4%)
Resolved by Repository

- astropy/astropy: 12/22 (54.55%)
- django/django: 176/231 (76.19%)
- matplotlib/matplotlib: 23/34 (67.65%)
- mwaskom/seaborn: 2/2 (100.0%)
- pallets/flask: 1/1 (100.0%)
- psf/requests: 7/8 (87.5%)
- pydata/xarray: 18/22 (81.82%)
- pylint-dev/pylint: 5/10 (50.0%)
- pytest-dev/pytest: 15/19 (78.95%)
- scikit-learn/scikit-learn: 27/32 (84.38%)
- sphinx-doc/sphinx: 31/44 (70.45%)
- sympy/sympy: 55/75 (73.33%)

---

Resolved by Time

- 2013: 3/3 (100.0%)
- 2014: 2/2 (100.0%)
- 2015: 1/1 (100.0%)
- 2016: 2/2 (100.0%)
- 2017: 13/16 (81.25%)
- 2018: 16/24 (66.67%)
- 2019: 76/98 (77.55%)
- 2020: 88/108 (81.48%)
- 2021: 56/86 (65.12%)
- 2022: 74/102 (72.55%)
- 2023: 41/58 (70.69%)

## Submission checklist

- [x] Is a pass@1 submission (does not attempt the same task instance more than once)
- [x] Does not use SWE-bench test knowledge (`PASS_TO_PASS`, `FAIL_TO_PASS`)
- [x] Does not use the `hints` field in SWE-bench
- [x] Does not have web-browsing OR has taken steps to prevent lookup of SWE-bench solutions via web-browsing

## To explore [Refact.ai Agent](http://refact.ai/)

- [Website](https://refact.ai/)
- [Github](https://github.com/smallcloudai/refact)
- Get for [VS Code](https://marketplace.visualstudio.com/items?itemName=smallcloud.codify) or [JetBrains](https://plugins.jetbrains.com/plugin/20647-refact--open-source-ai-agent-code-generator--chat)
- [Join the Discord](https://discord.com/invite/9GaWEK9Btb)
