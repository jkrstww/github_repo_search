#!/usr/bin/env bash
set -u

start="${1:-1551}"
end="${2:-2829}"
step="${STEP:-25}"
pause_seconds="${PAUSE_SECONDS:-3}"
per_page="${PER_PAGE:-50}"
retry_short_sleep="${RETRY_SHORT_SLEEP:-90}"
retry_long_sleep="${RETRY_LONG_SLEEP:-240}"
checkpoint="${CHECKPOINT:-data/repositories_harmony.checkpoint}"
lock_dir="${LOCK_DIR:-data/repositories_harmony.pull.lock}"

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "error: GITHUB_TOKEN is missing in WSL environment" >&2
  exit 1
fi

if ! mkdir "$lock_dir" 2>/dev/null; then
  echo "error: lock exists at $lock_dir; another pull may still be running" >&2
  exit 1
fi
trap 'rmdir "$lock_dir" 2>/dev/null || true' EXIT

current="$start"
touch "$checkpoint"
while [[ "$current" -le "$end" ]]; do
  batch_end=$((current + step - 1))
  if [[ "$batch_end" -gt "$end" ]]; then
    batch_end="$end"
  fi

  attempt=1
  while true; do
    if grep -qx "$current" "$checkpoint"; then
      echo "query ${current} already checkpointed; skipping"
      break
    fi

    echo "batch ${current}-${batch_end} attempt ${attempt} $(date +%Y-%m-%dT%H:%M:%S%z)"
    python3 run.py \
      --config config.harmony.json \
      --start-query-index "$current" \
      --end-query-index "$batch_end" \
      --per-page "$per_page" \
      --pause "$pause_seconds" \
      --quiet \
      --no-progress
    rc=$?
    if [[ "$rc" -eq 0 ]]; then
      if [[ "$current" -eq "$batch_end" ]]; then
        echo "$current" >> "$checkpoint"
      fi
      wc -l data/repositories_harmony.jsonl
      break
    fi

    if [[ "$attempt" -lt 5 ]]; then
      sleep_for="$retry_short_sleep"
    else
      sleep_for="$retry_long_sleep"
    fi
    echo "batch ${current}-${batch_end} failed rc=${rc}; retrying after ${sleep_for}s" >&2
    sleep "$sleep_for"
    attempt=$((attempt + 1))
  done

  current=$((batch_end + 1))
done

echo "all batches complete"
wc -l data/repositories_harmony.jsonl
ls -lh data/repositories_harmony.jsonl
