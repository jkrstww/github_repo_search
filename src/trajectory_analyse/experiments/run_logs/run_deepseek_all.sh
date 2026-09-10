#!/usr/bin/env bash
# Full scoring run: 1000 diff-trajectory pairs, judge=deepseek-v4-flash, concurrency=8.
# Outputs <instance>/<base>.score.deepseek-v4-flash.json; failures -> scoring_failures.jsonl.
# max_tokens=32768 (raised from 8192: long trajectories can need >8K completion tokens,
# else finish_reason=length truncates the rubric JSON).
# Resumable by default (skips existing valid scores). --force re-scores all.
set -euo pipefail
PY=/opt/miniconda3/bin/python
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$DIR"
FORCE="${FORCE:-}"
echo "[run] start $(date -u +%FT%TZ) judge=deepseek-v4-flash conc=8 max_tokens=32768 force=${FORCE:-no}"
"$PY" score_trajectories.py \
  --judge-backend api \
  --api-base "${DSK_API_BASE}" \
  --api-model "${DSK_API_MODEL}" \
  --api-key "${DSK_API_KEY}" \
  --api-max-tokens 32768 \
  --concurrency 8 \
  --timeout 300 \
  $FORCE
echo "[run] done  rc=$? $(date -u +%FT%TZ)"
