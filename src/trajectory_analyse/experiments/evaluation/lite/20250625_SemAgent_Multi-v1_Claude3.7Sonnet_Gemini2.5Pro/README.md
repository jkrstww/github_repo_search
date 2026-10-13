# SemAgent_Multi-v1

* SemAgent is a novel workflow-based procedure that leverages issue, code, and execution semantics to localize bugs and generate patches for complex software issues.
* Patch generation is driven by a semantics-aware process that harnesses Execution Flow Extraction and Step-Level Reasoning to produce precise and contextually informed fixes.
* Bug localization is driven by execution semantics generated from (1) crash reports produced by a generated reproducer, (2) execution traces from this reproducer, and (3) Spectrum-Based Fault Localization on buggy tests.
* SemAgent Multi runs three ablations of SemAgent and uses a final ranking system to determine the best patch and can be further understood from SemAgent's paper linked link.

### Link
[Link to Arxiv paper](https://arxiv.org/abs/2506.16650)

[Link to trajectories](https://github.com/AnvithPabba/SemAgent_Multi/tree/main/trajs/SWEBench_Lite/20250625_SemAgent_Multi-v1.0_Claude3.7Sonnet_Gemini2.5Pro)

### Abstract
Large Language Models (LLMs) have shown impressive capabilities in downstream software engineering tasks such as Automated Program Repair (APR). In particular, there has been a lot of research on repository-level issue-resolution benchmarks such as SWE-Bench. Although there has been significant progress on this topic, we notice that in the process of solving such issues, existing agentic systems tend to hyper-localize on immediately suspicious lines of code and fix them in isolation, without a deeper understanding of the issue semantics, code semantics, or execution semantics. Consequently, many existing systems generate patches that overfit to the user issue, even when a more general fix is preferable. To address this limitation, we introduce SemAgent, a novel workflow-based procedure that leverages issue, code, and execution semantics to generate patches that are complete - identifying and fixing all lines relevant to the issue. We achieve this through a novel pipeline that (a) leverages execution semantics to retrieve relevant context, (b) comprehends issue-semantics via generalized abstraction, (c) isolates code-semantics within the context of this abstraction, and (d) leverages this understanding in a two-stage architecture: a repair stage that proposes fine-grained fixes, followed by a reviewer stage that filters relevant fixes based on the inferred issue-semantics. We note that our approach performs particularly well on issues requiring multi-line reasoning (and editing) and edge-case handling, suggesting that incorporating issue and code semantics into APR pipelines can lead to robust and semantically consistent repairs.

## People Involved

Anvith Pabba [First Author (Anvith Pabba) LinkedIn](https://www.linkedin.com/in/anvith-pabba/), Alex Mathai, Anindya Chakraborty, Baishakhi Ray

## Checklist

Please copy paste this checklist in your `README.md` and confirm the following:
- [x] Is a pass@1 submission (does not attempt the same task instance more than once)
- [x] Does not use SWE-bench test knowledge (`PASS_TO_PASS`, `FAIL_TO_PASS`)
- [x] Does not use the `hints` field in SWE-bench
- [x] Does not have web-browsing OR has taken steps to prevent lookup of SWE-bench solutions via web-browsing

## Submission summary

Submission summary for 20250625_SemAgent_Multi-v1_Claude3.7Sonnet_Gemini2.5Pro on SWE-bench lite split
==================================================
Resolved 155 instances (51.67%)
==================================================
Resolved by Repository
- astropy/astropy: 3/6 (50.0%)
- django/django: 65/114 (57.02%)
- matplotlib/matplotlib: 13/23 (56.52%)
- mwaskom/seaborn: 1/4 (25.0%)
- pallets/flask: 1/3 (33.33%)
- psf/requests: 5/6 (83.33%)
- pydata/xarray: 1/5 (20.0%)
- pylint-dev/pylint: 3/6 (50.0%)
- pytest-dev/pytest: 8/17 (47.06%)
- scikit-learn/scikit-learn: 14/23 (60.87%)
- sphinx-doc/sphinx: 8/16 (50.0%)
- sympy/sympy: 33/77 (42.86%)
==================================================
Resolved by Time
- 2012: 1/1 (100.0%)
- 2014: 2/3 (66.67%)
- 2015: 1/1 (100.0%)
- 2016: 1/4 (25.0%)
- 2017: 5/16 (31.25%)
- 2018: 10/21 (47.62%)
- 2019: 32/59 (54.24%)
- 2020: 33/66 (50.0%)
- 2021: 25/42 (59.52%)
- 2022: 29/57 (50.88%)
- 2023: 16/30 (53.33%)
