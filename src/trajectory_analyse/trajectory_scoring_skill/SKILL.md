---
name: trajectory-scoring
description: Score software-engineering agent trajectories with an evidence-first, extensible rubric and task-specific case examples.
metadata:
  short-description: Evidence-first trajectory scoring
---

# Trajectory scoring

Use this skill when a trajectory must be scored as an engineering process rather than
judged only by its final `resolved` label. The workflow is deliberately modular:

1. Parse the complete trajectory and build a fact table.
2. Build an issue-specific oracle from the trajectory and, when available, the task's
   compiled case pack. Never fill unknown behavior from general software knowledge.
3. Score every rubric criterion independently. Attach direct trajectory evidence and
   at least one relevant positive or counterexample case when one is available. Prefer
   a complete `criterion_scores` audit trail (A1-F3) so each score point is inspectable;
   cases explain the anchor but never replace trajectory evidence.
4. Apply caps and flags only after raw criterion scores are complete.
5. Validate that the final JSON is structurally valid and that the six dimension scores
   sum to `total_score`.

Task case packs are optional. If `analyse.md` is absent, score observable process
evidence normally, mark task-specific oracle facts as unknown, add `NO_TASK_CASES`, and
lower evidence confidence when semantic correctness cannot be established. Absence of a
report is not itself a failed trajectory.

Use `rubric/base.yaml` for weights and criterion-level requirements, `cases/index.yaml`
for reusable examples, and `references/workflow.md` for the detailed phase contract.
