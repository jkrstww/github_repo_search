# Atlassian Rovo Dev

[Rovo Dev](https://www.atlassian.com/rovo-dev)
is Atlassian's AI-powered software development assistant designed to boost developer productivity using
expert software development capabilities and deep integration with your organization's knowledge base, code, and task
management system. We are developing specialized agents to help our customers with coding, code review, code planning,
and build/deployment, with much more to come.

## Performance on SWE-Bench Full

The current version of Rovo Dev Agent achieves 41.98% on SWE-Bench Full:

```
Submission summary for 20250605_atlassian-rovo-dev on SWE-bench test split
==================================================
Resolved 963 instances (41.98%)
==================================================
Resolved by Repository
- astropy/astropy: 30/95 (31.58%)
- django/django: 429/850 (50.47%)
- matplotlib/matplotlib: 69/184 (37.5%)
- mwaskom/seaborn: 10/22 (45.45%)
- pallets/flask: 5/11 (45.45%)
- psf/requests: 24/44 (54.55%)
- pydata/xarray: 36/110 (32.73%)
- pylint-dev/pylint: 11/57 (19.3%)
- pytest-dev/pytest: 55/119 (46.22%)
- scikit-learn/scikit-learn: 86/229 (37.55%)
- sphinx-doc/sphinx: 71/187 (37.97%)
- sympy/sympy: 137/386 (35.49%)
==================================================
Resolved by Time
- 2012: 2/2 (100.0%)
- 2013: 8/14 (57.14%)
- 2014: 8/11 (72.73%)
- 2015: 3/11 (27.27%)
- 2016: 12/24 (50.0%)
- 2017: 33/94 (35.11%)
- 2018: 64/171 (37.43%)
- 2019: 194/456 (42.54%)
- 2020: 191/438 (43.61%)
- 2021: 162/395 (41.01%)
- 2022: 179/418 (42.82%)
- 2023: 107/260 (41.15%)
```

## Submission Checklist

- [x] Is a pass@1 submission (does not attempt the same task instance more than once)
- [x] Does not use SWE-bench test knowledge (`PASS_TO_PASS`, `FAIL_TO_PASS`)
- [x] Does not use the `hints` field in SWE-bench
- [x] Does not have web-browsing OR has taken steps to prevent lookup of SWE-bench solutions via web-browsing

## Technical Report

The Rovo Dev Agent utilizes tool calling to navigate, plan, and resolve repo-level software development tasks.
This benchmark was achieved using a development version of Rovo Dev that includes minor differences from our production system, called out below.

For a detailed description of our foundational work on the Rovo Dev agent, please refer to [our paper published in ICSE 2025](https://arxiv.org/abs/2411.12924).
Since publication, we have moved to a purely agentic, rather than phased, approach, as described below.

### Tools

- View workspace / expand folder: Tools for viewing the file structure of the repo or subfolders
- Grep: A tool for searching file content across the entire repo (we use ripgrep under the hood)
- Open files: A tool that shows the agent a representation of a set of selected files. In most cases, we do not show the entirety of the file content, particularly for large files. Instead, we use a simple representation of the syntax tree based on (1) the previous actions take by the agent and (2) static analysis parsing of the code. See "Code Parsing" below.
- Inspect code: A tool for inspecting the context of specific code symbols or line ranges within a file
- Create file, delete file, find-and-replace code: Tools for code editing
- Bash: A tool for running bash commands (supports Powershell on Windows, but not relevant for SWE-Bench)
- Status: A tool that allows the agent to provide an indicator of the "phase" of the solution they are in (incomplete, verifying/testing, complete). This tool provides a structured way to extract reasoning from the agent on why a task is marked with a given status, and is also used to ensure that the agent run does not complete before the agent has marked the task as complete. If a trajectory is ended early (i.e., the task has not been marked as complete), the agent is re-prompted with `If you have fully completed the task, call the status function and mark it as 'complete'. Otherwise, please continue working on the task using the available functions.`

### Code Parsing

To enable more structured code retrieval, we have implemented a code parsing strategy that takes account of the agents previous actions as well as the structure of the code.

For example, if a file is opened by the agent after the agent has called grep on certain symbols, any structural sections (e.g., methods or functions) of the code file that contained
matches will be automatically shown, whereas other sections of the file will only show the syntax tree. This is achieved by breaking files down into semantically distinct sections
(such as functions, methods, and classes), checking for any relevant activity within each section and, if any is found, that section is highlighted in the tool response.

Similarly, portions of the code base that have been previously inspected or modified by the agent will be automatically highlighted when those files are opened by the agent.

These techniques enable the agent to more quickly identify relevant code without needing additional tool calls to traverse the code. Syntax trees are extracted using open source tree-sitter utilities.

### Tool Call Examples

Another simple modification made from our production system for evaluation is to initialize the agent trajectory with a single tool call example (which is always a call to the view workspace tool).
This provides useful information about the repo to the agent, and also provides a demonstration of the format/syntax that is required for tool calling, which prevents avoidable errors due to improperly formatted tool calls.

### Differences from the Rovo Dev product

The agent used for this benchmark did not have access to the internet, any of Atlassian's Jira, Confluence, or BitBucket data, or any other data outside of the repo itself. And there was no human-in-the-loop assistance.
