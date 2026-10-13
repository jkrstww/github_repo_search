#!/usr/bin/env bash
# Full scoring run, judge=GLM-5 via antchat OpenAI-compatible gateway.
# IMPORTANT: GLM-5 with response_format=json_object emits {{..}} (mustache-escaped) JSON that
# cannot be parsed -> must use --no-api-json-mode (it then emits fenced ```json instead).
# judge_id = GLM-5, written alongside deepseek's scores (judge_id separated).
# Outputs <instance>/<base>.score.GLM-5.json; failures -> scoring_failures.jsonl.
# Resumable by default (skips existing real scores); --force re-scores all.
set -euo pipefail
PY=/opt/miniconda3/bin/python
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$DIR"
FORCE="${FORCE:-}"
echo "[run-glm5] start $(date -u +%FT%TZ) judge=GLM-5 conc=8 max_tokens=32768 no-json-mode force=${FORCE:-no}"
"$PY" score_trajectories.py \
  --judge-backend api \
  --api-base "${GLM_API_BASE}" \
  --api-model "${GLM_API_MODEL}" \
  --api-key "${GLM_API_KEY}" \
  --api-max-tokens 32768 \
  --no-api-json-mode \
  --concurrency 8 \
  --timeout 300 \
  $FORCE
echo "[run-glm5] done  rc=$? $(date -u +%FT%TZ)"
