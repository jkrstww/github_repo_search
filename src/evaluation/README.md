# Track Evaluation

This package evaluates schema-v2 `message_track` files using three
deterministic metrics. It does not inspect hidden chain-of-thought and does not
use an LLM judge.

## 1. Action Validity

Action validity means whether a tool execution succeeded. The evaluator checks
explicit response fields in this order:

1. `success` (`true` or `false`);
2. `is_error` (inverted);
3. `exit_code` (`0` succeeds, non-zero fails);
4. `status` (`completed`, `success`, `succeeded`, `ok`, and `passed` succeed;
   `failed`, `error`, `cancelled`, `canceled`, and `timeout` fail);
5. a non-empty `error` field fails.

An action without one of these signals has an unknown outcome and is excluded
from the success-rate denominator. Calls and results with the same action ID
are merged, including Codex `command_execution` started/completed items.

The report contains total, known, unknown, successful, and failed action
counts, outcome coverage, and:

```text
tool_execution_success_rate = successful_actions / known_outcome_actions
```

This score only means that the tool executed successfully. It does not prove
that the action was relevant or contributed to the final solution.

## 2. Execution Rounds

One persisted agent response is one execution round. The evaluator reports:

- total response rounds;
- response rounds containing at least one tool action;
- response rounds without a tool action;
- the first and last response sequence numbers.

Output size and the number of items inside one response do not create extra
rounds.

## 3. Cross-file Retrieval

Expected files come from the instance metadata:

- the abstract definition path;
- the masked target path;
- `affected_modules`;
- `reference_implementation_files`.

A file is retrieved when its normalized repository-relative path appears in an
agent response. The primary metric is:

```text
file_recall = retrieved_expected_files / all_expected_files
```

Critical-file recall (abstract definition plus masked target) and reference-file
recall are reported separately. Extra files are not penalized because the
instance metadata is not a complete list of every potentially useful file.

## CLI

```bash
PYTHONPATH=src python -m evaluation \
  instance_tracks/<instance-name>/trajectory.json \
  --output instance_tracks/<instance-name>/evaluation.json
```

When `responses` is empty, response-dependent values return
`insufficient_data` and `null`, rather than a misleading zero score.
