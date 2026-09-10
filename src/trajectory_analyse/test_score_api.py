import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import score_trajectories as st


VALID_SCORE_JSON = json.dumps({
    "scores": {
        "A": {"name": "A", "score": 10}, "B": {"name": "B", "score": 10},
        "C": {"name": "C", "score": 20}, "D": {"name": "D", "score": 20},
        "E": {"name": "E", "score": 8}, "F": {"name": "F", "score": 8},
    },
    "raw_total_score": 76, "total_score": 76,
    "evidence_confidence": "medium", "caps_or_flags": [],
})


def _api_open(resp_obj):
    payload = json.dumps(resp_obj).encode("utf-8")
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.read = MagicMock(return_value=payload)
    return m


class HelpersTest(unittest.TestCase):
    def test_safe_model(self):
        self.assertEqual(st._safe_model("gpt-5.6-sol"), "gpt-5.6-sol")
        # unsafe chars -> '_', leading/trailing '._' stripped
        self.assertEqual(st._safe_model("glink/GLM-5.2:glink_domestic[1m]"),
                         "glink_GLM-5.2_glink_domestic_1m")
        self.assertEqual(st._safe_model(None), "api")
        self.assertEqual(st._safe_model(""), "api")

    def test_build_api_url(self):
        self.assertEqual(st._build_api_url("https://a.com/v1", "chat/completions"),
                         "https://a.com/v1/chat/completions")
        self.assertEqual(st._build_api_url("https://a.com/v1/", "/chat/completions"),
                         "https://a.com/v1/chat/completions")

    def test_first_text_openai(self):
        self.assertEqual(st._first_text({"choices": [{"message": {"content": "x"}}]}), "x")

    def test_first_text_anthropic(self):
        self.assertEqual(st._first_text({"content": [{"type": "text", "text": "y"}]}), "y")

    def test_first_text_empty(self):
        self.assertIsNone(st._first_text({}))
        self.assertIsNone(st._first_text({"choices": [{"message": {"content": ""}}]}))

    def test_first_text_falls_back_to_reasoning_content(self):
        # DeepSeek-style reasoning model: content empty, answer in reasoning_content
        resp = {"choices": [{"message": {"content": "", "reasoning_content": "RC"}}]}
        self.assertEqual(st._first_text(resp), "RC")
        # content takes priority when present
        resp2 = {"choices": [{"message": {"content": "C", "reasoning_content": "RC"}}]}
        self.assertEqual(st._first_text(resp2), "C")

    def test_inline_prompt_appends_trajectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.json"
            p.write_text('{"agent": {"model_name": "gpt"}}', encoding="utf-8")
            prompt = st._inline_prompt({"traj_path": str(p)}, "P {{TRAJECTORY_PATH}}", 0)
            self.assertIn("P {{TRAJECTORY_PATH}}".replace("{{TRAJECTORY_PATH}}", str(p)), prompt)
            self.assertIn('"agent": {"model_name": "gpt"}', prompt)
            self.assertIn("trajectory.json", prompt)

    def test_inline_prompt_truncates(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.json"
            p.write_text("X" * 5000, encoding="utf-8")
            prompt = st._inline_prompt({"traj_path": str(p)}, "P", 100)
            self.assertEqual(prompt.count("X"), 100)


class RunApiTest(unittest.TestCase):
    def _args(self, **over):
        base = dict(judge_backend="api", api_base="https://x/v1", api_model="gpt-5.6-sol",
                    api_key="tok", api_path="chat/completions", api_temperature=0.0,
                    api_max_tokens=32768, api_json_mode=True, api_truncate=0, timeout=60)
        base.update(over)
        return SimpleNamespace(**base)

    @patch("score_trajectories.urllib.request.urlopen")
    def test_sends_expected_request(self, urlopen):
        urlopen.return_value = _api_open({"choices": [{"message": {"content": VALID_SCORE_JSON}}]})
        text = st._run_api("hello", self._args())
        self.assertIn("total_score", text)
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "https://x/v1/chat/completions")
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.get_header("Authorization"), "Bearer tok")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["model"], "gpt-5.6-sol")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hello"}])
        self.assertEqual(body["temperature"], 0.0)
        self.assertEqual(body["response_format"], {"type": "json_object"})

    @patch("score_trajectories.urllib.request.urlopen")
    def test_no_json_mode_omits_response_format(self, urlopen):
        urlopen.return_value = _api_open({"choices": [{"message": {"content": VALID_SCORE_JSON}}]})
        st._run_api("h", self._args(api_json_mode=False))
        body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertNotIn("response_format", body)

    @patch("score_trajectories.urllib.request.urlopen")
    def test_http_error_becomes_runtime(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError(
            "u", 429, "Too Many", {}, io.BytesIO(b'{"error":"rate"}'))
        with self.assertRaises(RuntimeError) as cm:
            st._run_api("h", self._args())
        self.assertIn("HTTP 429", str(cm.exception))

    @patch("score_trajectories.urllib.request.urlopen")
    def test_empty_content_raises(self, urlopen):
        urlopen.return_value = _api_open({"choices": [{"message": {"content": ""}}]})
        with self.assertRaises(RuntimeError):
            st._run_api("h", self._args())

    @patch("score_trajectories.urllib.request.urlopen")
    def test_remote_disconnect_caught(self, urlopen):
        # http.client.RemoteDisconnected surfaces as a network RuntimeError, not "unhandled"
        import http.client
        urlopen.side_effect = http.client.RemoteDisconnected(
            "Remote end closed connection without response")
        with self.assertRaises(RuntimeError) as cm:
            st._run_api("h", self._args())
        self.assertIn("network error", str(cm.exception))
        self.assertIn("RemoteDisconnected", str(cm.exception))

    @patch("score_trajectories.time.sleep", return_value=None)
    @patch("score_trajectories.urllib.request.urlopen")
    def test_transient_recovers_after_retries(self, urlopen, sleep):
        # 2 transient RemoteDisconnected then a real completion -> returns text, sleeps x2
        import http.client
        ok = '{"choices":[{"message":{"content":"DONE"}}]}'
        urlopen.side_effect = [
            http.client.RemoteDisconnected("x"),
            http.client.RemoteDisconnected("y"),
            _Resp(ok.encode()),
        ]
        text = st._run_api("h", self._args())
        self.assertEqual(text, "DONE")
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("score_trajectories.time.sleep", return_value=None)
    @patch("score_trajectories.urllib.request.urlopen")
    def test_transient_exhausts_to_network_error(self, urlopen, sleep):
        import http.client
        urlopen.side_effect = http.client.RemoteDisconnected("x")
        with self.assertRaises(RuntimeError) as cm:
            st._run_api("h", self._args())
        self.assertIn("network error", str(cm.exception))
        self.assertEqual(urlopen.call_count, 3)  # default retries=3

    @patch("score_trajectories.time.sleep", return_value=None)
    @patch("score_trajectories.urllib.request.urlopen")
    def test_4xx_non_retryable_fails_fast(self, urlopen, sleep):
        urlopen.side_effect = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b"bad"))
        with self.assertRaises(RuntimeError) as cm:
            st._run_api("h", self._args())
        self.assertIn("HTTP 400", str(cm.exception))
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(sleep.call_count, 0)


class _Resp:
    """Minimal urlopen context-manager returning a fixed byte body."""
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


@patch("score_trajectories.urllib.request.urlopen")
class AsyncAttemptApiTest(unittest.TestCase):
    def _args(self, tmp, **over):
        base = dict(judge_backend="api", api_base="https://x/v1", api_model="gpt-5.6-sol",
                    api_key="tok", api_path="chat/completions", api_temperature=0.0,
                    api_max_tokens=8192, api_json_mode=True, api_truncate=0,
                    timeout=60, force=True, judge_id=None, trajs_root=tmp)
        base.update(over)
        return SimpleNamespace(**base)

    def test_attempt_api_returns_parsed_obj(self, urlopen):
        urlopen.return_value = _api_open({"choices": [{"message": {"content": VALID_SCORE_JSON}}]})
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            traj = tmp / "i" / "m.json"
            traj.parent.mkdir()
            traj.write_text('{"agent":{"model_name":"gpt"}}', encoding="utf-8")
            pair = {"instance": "i", "submission": "m", "traj_path": str(traj),
                    "resolved": True, "exists": True,
                    "score_path": str(tmp / "i" / "m.score.json")}
            out = st._attempt(pair, "P {{TRAJECTORY_PATH}}", self._args(tmp))
            self.assertIsInstance(out, dict)
            self.assertEqual(out["total_score"], 76)

    def test_attempt_api_runtime_error(self, urlopen):
        # 400 is non-transient (payment/contract), so it fails at once with HTTP 4xx
        urlopen.side_effect = urllib.error.HTTPError(
            "u", 400, "err", {}, io.BytesIO(b"boom"))
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            traj = tmp / "i" / "m.json"
            traj.parent.mkdir()
            traj.write_text('{}', encoding="utf-8")
            pair = {"instance": "i", "submission": "m", "traj_path": str(traj),
                    "resolved": True, "exists": True,
                    "score_path": str(tmp / "i" / "m.score.json")}
            out = st._attempt(pair, "P", self._args(tmp))
            self.assertEqual(out, "runtime:HTTP 400: boom")


class ParseArgsApiTest(unittest.TestCase):
    def test_api_requires_base_model_key(self):
        # each of --api-base / --api-model / --api-key (and its env) missing -> exit
        with self.assertRaises(SystemExit):
            st.parse_args(["--judge-backend", "api", "--api-model", "m",
                           "--api-key", "k", "--timeout", "5"])  # no --api-base
        with self.assertRaises(SystemExit):
            st.parse_args(["--judge-backend", "api", "--api-base", "u",
                           "--api-key", "k", "--timeout", "5"])  # no --api-model
        # no --api-key AND the default --api-key-env (OPENAI_API_KEY) unset -> exit
        old = os.environ.pop("OPENAI_API_KEY", None)
        try:
            with self.assertRaises(SystemExit):
                st.parse_args(["--judge-backend", "api", "--api-base", "u",
                               "--api-model", "m", "--timeout", "5"])
        finally:
            if old is not None:
                os.environ["OPENAI_API_KEY"] = old

    def test_api_key_from_env(self):
        old = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "envtok"
        try:
            a = st.parse_args(["--judge-backend", "api", "--api-base", "u",
                               "--api-model", "m", "--timeout", "5"])
            self.assertEqual(a.api_key, "envtok")
        finally:
            if old is not None:
                os.environ["OPENAI_API_KEY"] = old
            else:
                del os.environ["OPENAI_API_KEY"]

    def test_codex_backend_default(self):
        a = st.parse_args(["--timeout", "5"])
        self.assertEqual(a.judge_backend, "codex")


if __name__ == "__main__":
    unittest.main()
