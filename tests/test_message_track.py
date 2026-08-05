from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluation import evaluate_track
from message_track import MessageTrack, build_tool_action, load_message_track


class FakeSdkResponse:
    def model_dump(self, *, mode: str) -> dict[str, object]:
        return {
            "id": "resp_sdk",
            "status": "completed",
            "output": [{"type": "message", "text": "SDK response"}],
            "serialization_mode": mode,
        }


class MessageTrackTest(unittest.TestCase):
    def test_default_output_uses_instance_directory_under_track_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            instance_dir = root / "instances" / "feature_implement" / "instance-1"
            instance_dir.mkdir(parents=True)
            (instance_dir / "instance.json").write_text(
                json.dumps(
                    {
                        "task_type": "feature_implementation",
                        "instance_id": "instance-1",
                    }
                ),
                encoding="utf-8",
            )

            track = MessageTrack(instance_dir)

            expected = root / "instance_tracks" / "instance-1" / "trajectory.json"
            self.assertEqual(track.output_path, expected)
            self.assertTrue(expected.is_file())
            self.assertEqual(load_message_track(expected)["responses"], [])

    def test_records_complete_raw_response_and_preserves_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            instance_dir = Path(temporary_directory) / "legado-instance"
            instance_dir.mkdir()
            (instance_dir / "instance.json").write_text(
                json.dumps(
                    {
                        "task_type": "feature_implementation",
                        "instance_id": "legado-instance",
                        "target": {"abstract_node": {"name": "BasicDataSource"}},
                    }
                ),
                encoding="utf-8",
            )
            output = Path(temporary_directory) / "track" / "trajectory.json"
            response = {
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "已补全实现"}],
                    },
                    {
                        "type": "function_call",
                        "name": "shell_command",
                        "arguments": {"command": "git diff"},
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

            track = MessageTrack(instance_dir, output_path=output)
            record = track.record_response(response, metadata={"turn": 1})
            track.close(summary="done")

            document = load_message_track(output)
            self.assertEqual(record.response_id, "resp_1")
            self.assertEqual(document["status"], "completed")
            self.assertNotIn("events", document)
            self.assertEqual(document["responses"][0]["response"], response)
            self.assertEqual(
                document["responses"][0]["response"]["output"][0]["content"][0]["text"],
                "已补全实现",
            )

    def test_accepts_sdk_response_objects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "trajectory.json"
            track = MessageTrack(
                Path(temporary_directory) / "instance",
                output_path=output,
                instance_id="instance-1",
            )
            track.record_response(FakeSdkResponse())

            response = load_message_track(output)["responses"][0]["response"]
            self.assertEqual(response["id"], "resp_sdk")
            self.assertEqual(response["serialization_mode"], "json")

    def test_records_provider_neutral_turn_for_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            instance_dir = (
                Path(temporary_directory)
                / "instances"
                / "feature_implement"
                / "instance-1"
            )
            instance_dir.mkdir(parents=True)
            expected_file = "entry/src/main/ets/common/LazyIDataSource/BasicDataSource.ets"
            target_file = "entry/src/main/ets/common/LazyIDataSource/ChaptersDataSource.ets"
            reference_file = "entry/src/main/ets/common/LazyIDataSource/FileListDataSource.ets"
            (instance_dir / "instance.json").write_text(
                json.dumps(
                    {
                        "task_type": "feature_implementation",
                        "instance_id": "instance-1",
                        "target": {"abstract_node": {"path": expected_file}},
                        "mask": {"path": target_file},
                        "reference_implementation_files": [reference_file],
                        "affected_modules": [
                            expected_file,
                            target_file,
                            reference_file,
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = Path(temporary_directory) / "trajectory.json"
            track = MessageTrack(instance_dir, output_path=output)

            track.record_turn(
                message=f"先查看 {expected_file}，再恢复 {target_file}",
                actions=[
                    build_tool_action(
                        action_type="command_execution",
                        action_id="cmd_1",
                        command=f"sed -n '1,80p' {expected_file}",
                        status="completed",
                        exit_code=0,
                        output="source content",
                    )
                ],
                file_paths=[reference_file],
                metadata={"turn": 1},
            )

            document = load_message_track(output)
            self.assertEqual(document["responses"][0]["response_id"], "turn_1")
            self.assertEqual(
                document["responses"][0]["response"]["output"][1]["type"],
                "command_execution",
            )

            metrics = evaluate_track(document)["metrics"]
            self.assertEqual(
                metrics["action_validity"]["tool_execution_success_rate"], 1.0
            )
            self.assertEqual(metrics["cross_file_retrieval"]["file_recall"], 1.0)

    def test_resumes_and_preserves_response_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "trajectory.json"
            instance = Path(temporary_directory) / "instance"
            first = MessageTrack(
                output_path=output,
                instance_dir=instance,
                instance_id="instance-1",
            )
            first.record_response({"id": "resp_1", "output_text": "first"})

            resumed = MessageTrack(
                output_path=output,
                instance_dir=instance,
                instance_id="instance-1",
            )
            resumed.record_response({"id": "resp_2", "output_text": "second"})

            responses = load_message_track(output)["responses"]
            self.assertEqual([item["seq"] for item in responses], [1, 2])
            self.assertEqual(responses[-1]["response_id"], "resp_2")

    def test_context_manager_marks_exception_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "trajectory.json"
            with self.assertRaises(RuntimeError):
                with MessageTrack(
                    Path(temporary_directory) / "instance", output_path=output
                ):
                    raise RuntimeError("agent failed")

            document = load_message_track(output)
            self.assertEqual(document["status"], "failed")
            self.assertEqual(document["responses"], [])


if __name__ == "__main__":
    unittest.main()
