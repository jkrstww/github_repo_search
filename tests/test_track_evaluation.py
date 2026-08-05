from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluation import evaluate_track, evaluate_track_file, metric_feasibility


def make_track() -> dict[str, object]:
    base = "entry/src/main/ets/common/LazyIDataSource"
    return {
        "schema_version": 2,
        "track_id": "track-1",
        "instance_id": "legado-instance",
        "status": "completed",
        "task_metadata": {
            "target": {
                "abstract_node": {
                    "path": f"{base}/BasicDataSource.ets",
                    "name": "BasicDataSource",
                }
            },
            "mask": {"path": f"{base}/ChaptersDataSource.ets"},
            "reference_implementation_files": [
                f"{base}/FileListDataSource.ets",
                f"{base}/rssSourcesDataSource.ets",
            ],
            "affected_modules": [
                f"{base}/BasicDataSource.ets",
                f"{base}/ChaptersDataSource.ets",
                f"{base}/FileListDataSource.ets",
                f"{base}/rssSourcesDataSource.ets",
            ],
        },
        "responses": [
            {
                "seq": 1,
                "response": {
                    "id": "resp_1",
                    "output": [
                        {
                            "type": "command_execution",
                            "id": "cmd_1",
                            "command": "rg BasicDataSource",
                            "status": "failed",
                            "exit_code": 1,
                            "aggregated_output": "not found",
                        },
                        {
                            "type": "message",
                            "text": f"Inspect {base}/BasicDataSource.ets.",
                        },
                    ],
                },
            },
            {
                "seq": 2,
                "response": {
                    "id": "resp_2",
                    "output": [
                        {
                            "type": "command_execution",
                            "id": "cmd_2",
                            "command": "Get-Content FileListDataSource.ets",
                            "status": "completed",
                            "exit_code": 0,
                        },
                        {
                            "type": "message",
                            "text": (
                                f"Use {base}/FileListDataSource.ets to restore "
                                f"{base}/ChaptersDataSource.ets."
                            ),
                        },
                    ],
                },
            },
        ],
    }


class TrackEvaluationTest(unittest.TestCase):
    def test_only_three_metrics_are_exposed(self) -> None:
        expected = {
            "action_validity",
            "execution_rounds",
            "cross_file_retrieval",
        }

        self.assertEqual(set(metric_feasibility()), expected)
        self.assertEqual(set(evaluate_track(make_track())["metrics"]), expected)

    def test_action_validity_uses_observed_tool_execution_outcomes(self) -> None:
        metric = evaluate_track(make_track())["metrics"]["action_validity"]

        self.assertEqual(metric["total_actions"], 2)
        self.assertEqual(metric["known_outcome_actions"], 2)
        self.assertEqual(metric["unknown_outcome_actions"], 0)
        self.assertEqual(metric["successful_actions"], 1)
        self.assertEqual(metric["failed_actions"], 1)
        self.assertEqual(metric["outcome_coverage"], 1.0)
        self.assertEqual(metric["tool_execution_success_rate"], 0.5)

    def test_unknown_action_outcome_is_excluded_from_success_rate(self) -> None:
        track = make_track()
        track["responses"] = [
            {
                "seq": 1,
                "response": {
                    "output": [
                        {
                            "type": "function_call",
                            "id": "call_1",
                            "name": "read_file",
                            "arguments": {"path": "README.md"},
                        }
                    ]
                },
            }
        ]

        metric = evaluate_track(track)["metrics"]["action_validity"]

        self.assertEqual(metric["total_actions"], 1)
        self.assertEqual(metric["unknown_outcome_actions"], 1)
        self.assertEqual(metric["outcome_coverage"], 0.0)
        self.assertIsNone(metric["tool_execution_success_rate"])

    def test_merges_started_and_completed_command_by_id(self) -> None:
        track = make_track()
        track["responses"] = [
            {
                "seq": 1,
                "response": {
                    "output": [
                        {
                            "type": "command_execution",
                            "id": "cmd_1",
                            "command": "rg BasicDataSource",
                            "status": "in_progress",
                        }
                    ]
                },
            },
            {
                "seq": 2,
                "response": {
                    "output": [
                        {
                            "type": "command_execution",
                            "id": "cmd_1",
                            "command": "rg BasicDataSource",
                            "status": "completed",
                            "exit_code": 0,
                        }
                    ]
                },
            },
        ]

        metric = evaluate_track(track)["metrics"]["action_validity"]

        self.assertEqual(metric["total_actions"], 1)
        self.assertEqual(metric["successful_actions"], 1)

    def test_counts_response_and_action_rounds(self) -> None:
        metric = evaluate_track(make_track())["metrics"]["execution_rounds"]

        self.assertEqual(metric["total_response_rounds"], 2)
        self.assertEqual(metric["action_response_rounds"], 2)
        self.assertEqual(metric["non_action_response_rounds"], 0)
        self.assertEqual(metric["first_response_seq"], 1)
        self.assertEqual(metric["last_response_seq"], 2)

    def test_computes_expected_file_recall_only(self) -> None:
        metric = evaluate_track(make_track())["metrics"]["cross_file_retrieval"]

        self.assertEqual(len(metric["expected_files"]), 4)
        self.assertEqual(len(metric["retrieved_files"]), 3)
        self.assertEqual(metric["file_recall"], 0.75)
        self.assertEqual(metric["critical_file_recall"], 1.0)
        self.assertEqual(metric["reference_file_recall"], 0.5)
        self.assertEqual(
            metric["missing_files"],
            [
                "entry/src/main/ets/common/LazyIDataSource/"
                "rssSourcesDataSource.ets"
            ],
        )
        self.assertNotIn("symbol_recall", metric)

    def test_empty_track_returns_insufficient_data_and_null_rates(self) -> None:
        track = make_track()
        track["responses"] = []

        metrics = evaluate_track(track)["metrics"]

        self.assertEqual(
            metrics["action_validity"]["applicability"], "insufficient_data"
        )
        self.assertIsNone(
            metrics["action_validity"]["tool_execution_success_rate"]
        )
        self.assertEqual(
            metrics["execution_rounds"]["applicability"], "insufficient_data"
        )
        self.assertEqual(
            metrics["cross_file_retrieval"]["applicability"],
            "insufficient_data",
        )
        self.assertIsNone(metrics["cross_file_retrieval"]["file_recall"])

    def test_writes_evaluation_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            track_path = root / "trajectory.json"
            output_path = root / "evaluation.json"
            track_path.write_text(
                json.dumps(make_track(), ensure_ascii=False), encoding="utf-8"
            )

            result = evaluate_track_file(track_path, output_path=output_path)
            persisted = json.loads(output_path.read_text(encoding="utf-8"))

            self.assertEqual(persisted["schema_version"], 2)
            self.assertEqual(persisted["instance_id"], "legado-instance")
            self.assertEqual(result["instance_id"], persisted["instance_id"])


if __name__ == "__main__":
    unittest.main()
