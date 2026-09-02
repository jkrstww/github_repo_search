from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.track_codex_trajectory import (
    _native_session_is_complete,
    build_run_command,
    find_session_path,
    track_trajectory,
    thread_id_from_event,
)


class TrackCodexTrajectoryTest(unittest.TestCase):
    def test_extracts_thread_id_from_native_start_event(self) -> None:
        event = {
            "type": "thread.started",
            "thread_id": "019efeb0-f774-7880-95a8-82b13a5e0e45",
        }

        self.assertEqual(
            thread_id_from_event(event), "019efeb0-f774-7880-95a8-82b13a5e0e45"
        )

    def test_ignores_other_events_and_invalid_thread_ids(self) -> None:
        self.assertIsNone(thread_id_from_event({"type": "turn.started"}))
        self.assertIsNone(
            thread_id_from_event({"type": "thread.started", "thread_id": "../bad"})
        )

    def test_finds_native_session_file(self) -> None:
        thread_id = "019efeb0-f774-7880-95a8-82b13a5e0e45"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            session = root / "2026" / "09" / "01" / f"rollout-x-{thread_id}.jsonl"
            session.parent.mkdir(parents=True)
            session.write_text(
                '{"type":"session_meta","payload":{"id":"'
                + thread_id
                + '"}}\n'
                '{"type":"event_msg","payload":{"type":"task_complete"}}\n',
                encoding="utf-8",
            )

            self.assertEqual(find_session_path(thread_id, sessions_root=root), session)

    def test_run_command_uses_native_json_and_explicit_workspace(self) -> None:
        command = build_run_command(
            "/bin/codex",
            workspace=Path("/tmp/example workspace"),
            instruction="实现画折线图的功能",
            model="gpt-5.6",
        )

        self.assertEqual(
            command,
            [
                "/bin/codex",
                "--ask-for-approval",
                "never",
                "exec",
                "--json",
                "--model",
                "gpt-5.6",
                "--cd",
                "/tmp/example workspace",
                "--skip-git-repo-check",
                "--sandbox",
                "workspace-write",
                "实现画折线图的功能",
            ],
        )

    def test_tracks_events_and_copies_native_session(self) -> None:
        thread_id = "019efeb0-f774-7880-95a8-82b13a5e0e45"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions_root = root / "sessions"
            source_session = (
                sessions_root / "2026" / "09" / "01" / f"rollout-x-{thread_id}.jsonl"
            )
            source_session.parent.mkdir(parents=True)
            source_session.write_text(
                '{"type":"session_meta","payload":{"id":"'
                + thread_id
                + '"}}\n'
                '{"type":"event_msg","payload":{"type":"task_complete"}}\n',
                encoding="utf-8",
            )
            executable = root / "fake_codex.py"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                f"print(json.dumps({{'type': 'thread.started', 'thread_id': '{thread_id}'}}))\n"
                "print(json.dumps({'type': 'turn.completed'}))\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)

            metadata_path, exit_code = track_trajectory(
                workspace=root,
                instruction="do the task",
                model="gpt-test",
                output_dir=root / "output",
                executable=str(executable),
                sessions_root=sessions_root,
            )

            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(metadata["status"], "completed")
            self.assertEqual(metadata["thread_id"], thread_id)
            self.assertEqual(metadata["session_id"], thread_id)
            self.assertEqual(metadata["event_count"], 2)
            session = metadata_path.parent / metadata["artifacts"]["session"]
            self.assertEqual(session.read_text(encoding="utf-8"), source_session.read_text(encoding="utf-8"))

    def test_native_session_requires_matching_completion_event(self) -> None:
        thread_id = "019efeb0-f774-7880-95a8-82b13a5e0e45"
        with tempfile.TemporaryDirectory() as temporary_directory:
            session = Path(temporary_directory) / "session.jsonl"
            session.write_text(
                '{"type":"session_meta","payload":{"id":"other-thread"}}\n'
                '{"type":"event_msg","payload":{"type":"task_complete"}}\n',
                encoding="utf-8",
            )
            self.assertFalse(_native_session_is_complete(session, thread_id))


if __name__ == "__main__":
    unittest.main()
