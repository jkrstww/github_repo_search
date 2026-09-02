from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.track_opencode_trajectory import (
    build_run_command,
    resolve_model,
    session_id_from_event,
)


class TrackOpenCodeTrajectoryTest(unittest.TestCase):
    @patch("tools.track_opencode_trajectory.subprocess.run")
    def test_resolves_display_model_name(self, run: unittest.mock.Mock) -> None:
        run.return_value = subprocess.CompletedProcess(
            ["opencode", "models"],
            0,
            stdout="opencode/kimi-k2.5\nopencode/kimi-k3\n",
            stderr="",
        )

        resolved = resolve_model("opencode", "Kimi K3", cwd=Path("."))

        self.assertEqual(resolved, "opencode/kimi-k3")

    @patch("tools.track_opencode_trajectory.subprocess.run")
    def test_provider_model_is_used_without_listing_models(
        self, run: unittest.mock.Mock
    ) -> None:
        resolved = resolve_model(
            "opencode", "opencode/kimi-k3", cwd=Path(".")
        )

        self.assertEqual(resolved, "opencode/kimi-k3")
        run.assert_not_called()

    def test_extracts_nested_session_id(self) -> None:
        event = {
            "type": "tool_use",
            "properties": {"sessionID": "ses_123", "name": "read"},
        }

        self.assertEqual(session_id_from_event(event), "ses_123")

    def test_run_command_uses_native_json_and_explicit_workspace(self) -> None:
        command = build_run_command(
            "/bin/opencode",
            workspace=Path("/tmp/example workspace"),
            instruction="实现画折线图的功能",
            model="opencode/kimi-k3",
        )

        self.assertEqual(
            command,
            [
                "/bin/opencode",
                "run",
                "--format",
                "json",
                "--model",
                "opencode/kimi-k3",
                "--dir",
                "/tmp/example workspace",
                "实现画折线图的功能",
            ],
        )

    def test_ignores_unrelated_session_values(self) -> None:
        self.assertIsNone(session_id_from_event({"session": "not-an-id"}))


if __name__ == "__main__":
    unittest.main()
