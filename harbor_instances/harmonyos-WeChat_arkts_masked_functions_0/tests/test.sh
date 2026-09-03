#!/bin/bash
set -uo pipefail
cd /workspace/repo
git apply --check /tests/f2p_patch.diff && git apply /tests/f2p_patch.diff
python -m pytest -q tests/test_outputs.py
code=$?
mkdir -p /logs/verifier
if [ $code -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
exit 0
