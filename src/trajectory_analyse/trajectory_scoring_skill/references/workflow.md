# Workflow contract

## 1. Discover

Read the full trajectory, preserving step order, tool command, raw output, exit code,
file changes, tests, and final patch state. Do not use a sibling trajectory or an
external `resolved` label as evidence for the current trajectory.

## 2. Build the oracle

Record positive behavior, negative/compatibility behavior, boundaries, forbidden changes,
and unknowns. A task case pack can add facts learned from `analyse.md`; it cannot turn a
missing fact into a certainty. The case pack is context, not a replacement for direct
trajectory evidence.

## 3. Score criteria

For each criterion in `rubric/base.yaml`, record: score, max score, level, direct evidence,
case references, and missing evidence. The preferred output is a `criterion_scores` array
with one entry for every A1-F3 criterion; each entry should include `case_refs` and the
trajectory locations that justify the score. Use a positive example to define the upper
anchor and a counterexample or boundary example to define the deduction boundary. Do not
count the same observation twice inside one dimension. If no applicable case exists, say
so explicitly and use the current trajectory only; do not invent a case.

## 4. Apply caps

Compute raw scores first. Apply the existing caps and flags only after scoring. If a cap
requires reducing the total, reduce the dimensions named by the cap and make the reason
visible in `key_risks` or `evidence_gaps`.

## 5. Validate

Require six dimensions A-F, numeric scores within their maxima, `total_score` equal to
their sum (within 0.1), and evidence locations that can be found in the trajectory or a
case pack. For malformed or unreadable trajectories, return the normal zero-score schema
with a low confidence and a parse error in `evidence_gaps`.
