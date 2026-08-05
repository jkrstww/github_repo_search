from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


EVALUATION_SCHEMA_VERSION = 2
SUCCESS_STATUSES = {"completed", "success", "succeeded", "ok", "passed"}
FAILURE_STATUSES = {"failed", "error", "cancelled", "canceled", "timeout"}
CALL_TYPES = {"function_call", "tool_call"}
RESULT_TYPES = {"function_call_output", "tool_result", "tool_output"}
COMMAND_TYPES = {"command_execution", "command_result"}


@dataclass(frozen=True)
class ActionObservation:
    """One tool action with an optional observed execution outcome."""

    response_seq: int
    action_id: str | None
    tool_name: str
    signature: str
    success: bool | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_seq": self.response_seq,
            "action_id": self.action_id,
            "tool_name": self.tool_name,
            "signature": self.signature,
            "success": self.success,
            "detail": self.detail,
        }


def metric_feasibility() -> dict[str, dict[str, Any]]:
    """Return the evidence requirements for the three supported metrics."""
    return {
        "action_validity": {
            "feasibility": "high_with_observed_tool_outcomes",
            "requires": ["tool calls", "explicit status, success, error, or exit code"],
            "limitation": (
                "Success means that the tool executed successfully. It does not prove "
                "that the action contributed to solving the benchmark task."
            ),
        },
        "execution_rounds": {
            "feasibility": "high",
            "requires": ["ordered response records"],
            "limitation": (
                "A round is one persisted agent response, independent of response size "
                "or number of output items."
            ),
        },
        "cross_file_retrieval": {
            "feasibility": "high_for_benchmark_file_recall",
            "requires": ["instance expected file paths", "agent responses"],
            "limitation": (
                "Recall measures whether benchmark-known file paths appeared in agent "
                "responses. It does not judge whether additional files were useful."
            ),
        },
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dicts(item)


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def _response_records(track: Mapping[str, Any]) -> list[dict[str, Any]]:
    responses = track.get("responses", [])
    if not isinstance(responses, list):
        raise ValueError("track responses must be a list")
    result: list[dict[str, Any]] = []
    for index, record in enumerate(responses, start=1):
        if not isinstance(record, dict):
            raise ValueError("each response record must be an object")
        result.append(
            {
                "seq": int(record.get("seq", index)),
                "response": record.get("response"),
            }
        )
    return result


def _node_type(node: Mapping[str, Any]) -> str:
    return str(node.get("type", "")).strip().lower()


def _action_id(node: Mapping[str, Any]) -> str | None:
    value = node.get("call_id") or node.get("tool_call_id") or node.get("id")
    return str(value) if value is not None else None


def _tool_name(node: Mapping[str, Any], node_type: str) -> str:
    value = node.get("name") or node.get("tool") or node.get("tool_name")
    if value is not None:
        return str(value)
    if node_type in COMMAND_TYPES:
        return "shell_command"
    return "unknown_tool"


def _action_signature(node: Mapping[str, Any], tool_name: str) -> str:
    arguments = node.get("arguments")
    if arguments is None:
        arguments = node.get("input")
    if arguments is None:
        arguments = node.get("command")
    if arguments is None:
        return tool_name
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    return f"{tool_name}:{arguments.strip()}"


def _execution_outcome(node: Mapping[str, Any], node_type: str) -> bool | None:
    """Infer only whether the tool execution itself succeeded."""
    if isinstance(node.get("success"), bool):
        return bool(node["success"])
    if isinstance(node.get("is_error"), bool):
        return not bool(node["is_error"])
    exit_code = node.get("exit_code")
    if isinstance(exit_code, int):
        return exit_code == 0
    status = str(node.get("status", "")).strip().lower()
    if status in SUCCESS_STATUSES:
        return True
    if status in FAILURE_STATUSES:
        return False
    if node.get("error") not in (None, "", False):
        return False
    if node_type in RESULT_TYPES and "output" in node:
        return None
    return None


def _detail(node: Mapping[str, Any]) -> str:
    for key in ("error", "aggregated_output", "output", "result", "command"):
        value = node.get(key)
        if value not in (None, ""):
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            return text[:500]
    return ""


def extract_actions(track: Mapping[str, Any]) -> list[ActionObservation]:
    """Extract tool actions and merge started/completed items by action ID."""
    observations: list[ActionObservation] = []
    pending: dict[str, ActionObservation] = {}
    for record in _response_records(track):
        seq = record["seq"]
        for node in _iter_dicts(record["response"]):
            node_type = _node_type(node)
            if node_type not in CALL_TYPES | RESULT_TYPES | COMMAND_TYPES:
                continue
            action_id = _action_id(node)
            tool_name = _tool_name(node, node_type)
            success = _execution_outcome(node, node_type)
            observation = ActionObservation(
                response_seq=seq,
                action_id=action_id,
                tool_name=tool_name,
                signature=_action_signature(node, tool_name),
                success=success,
                detail=_detail(node),
            )
            if node_type in CALL_TYPES | COMMAND_TYPES and success is None:
                observations.append(observation)
                if action_id is not None:
                    pending[action_id] = observation
                continue
            if action_id in pending and success is not None:
                original = pending.pop(action_id)
                for index in range(len(observations) - 1, -1, -1):
                    if observations[index] is original:
                        observations[index] = ActionObservation(
                            response_seq=seq,
                            action_id=action_id,
                            tool_name=original.tool_name,
                            signature=original.signature,
                            success=success,
                            detail=_detail(node),
                        )
                        break
                continue
            observations.append(observation)
    return observations


def evaluate_action_validity(track: Mapping[str, Any]) -> dict[str, Any]:
    actions = extract_actions(track)
    known = [action for action in actions if action.success is not None]
    successful = [action for action in known if action.success]
    failed = [action for action in known if not action.success]
    if not actions:
        applicability = (
            "not_applicable" if _response_records(track) else "insufficient_data"
        )
    else:
        applicability = "applicable"
    return {
        "applicability": applicability,
        "total_actions": len(actions),
        "known_outcome_actions": len(known),
        "unknown_outcome_actions": len(actions) - len(known),
        "successful_actions": len(successful),
        "failed_actions": len(failed),
        "outcome_coverage": len(known) / len(actions) if actions else None,
        "tool_execution_success_rate": (
            len(successful) / len(known) if known else None
        ),
        "actions": [action.to_dict() for action in actions],
    }


def evaluate_execution_rounds(track: Mapping[str, Any]) -> dict[str, Any]:
    responses = _response_records(track)
    actions = extract_actions(track)
    action_rounds = {action.response_seq for action in actions}
    return {
        "applicability": "applicable" if responses else "insufficient_data",
        "total_response_rounds": len(responses),
        "action_response_rounds": len(action_rounds),
        "non_action_response_rounds": len(responses) - len(action_rounds),
        "first_response_seq": responses[0]["seq"] if responses else None,
        "last_response_seq": responses[-1]["seq"] if responses else None,
    }


def _unique_paths(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        normalised = value.replace("\\", "/")
        if normalised not in seen:
            seen.add(normalised)
            result.append(normalised)
    return result


def _expected_files(track: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
    metadata = track.get("task_metadata", {})
    if not isinstance(metadata, dict):
        return [], [], []
    target = metadata.get("target", {}) if isinstance(metadata.get("target"), dict) else {}
    abstract = (
        target.get("abstract_node", {})
        if isinstance(target.get("abstract_node"), dict)
        else {}
    )
    mask = metadata.get("mask", {}) if isinstance(metadata.get("mask"), dict) else {}
    critical = _unique_paths([abstract.get("path"), mask.get("path")])
    references = _unique_paths(metadata.get("reference_implementation_files", []))
    affected = _unique_paths(metadata.get("affected_modules", []))
    all_files = _unique_paths([*critical, *references, *affected])
    return all_files, critical, references


def evaluate_cross_file_retrieval(track: Mapping[str, Any]) -> dict[str, Any]:
    expected, critical, references = _expected_files(track)
    response_records = _response_records(track)
    has_responses = bool(response_records)
    corpus = "\n".join(
        _iter_strings([record["response"] for record in response_records])
    )
    normalised_corpus = corpus.replace("\\", "/").lower()

    def recalled(paths: list[str]) -> list[str]:
        return [path for path in paths if path.lower() in normalised_corpus]

    retrieved = recalled(expected)
    retrieved_critical = recalled(critical)
    retrieved_references = recalled(references)
    file_recall = len(retrieved) / len(expected) if expected and has_responses else None
    critical_recall = (
        len(retrieved_critical) / len(critical) if critical and has_responses else None
    )
    reference_recall = (
        len(retrieved_references) / len(references)
        if references and has_responses
        else None
    )
    return {
        "applicability": (
            "applicable"
            if has_responses and expected
            else "insufficient_data"
            if not has_responses
            else "not_applicable"
        ),
        "expected_files": expected,
        "retrieved_files": retrieved,
        "missing_files": [path for path in expected if path not in retrieved],
        "file_recall": file_recall,
        "expected_critical_files": critical,
        "retrieved_critical_files": retrieved_critical,
        "critical_file_recall": critical_recall,
        "expected_reference_files": references,
        "retrieved_reference_files": retrieved_references,
        "reference_file_recall": reference_recall,
    }


def evaluate_track(track: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate only tool validity, response rounds, and file retrieval recall."""
    if not isinstance(track, Mapping):
        raise ValueError("track must be an object")
    responses = _response_records(track)
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "instance_id": track.get("instance_id"),
        "track_id": track.get("track_id"),
        "evaluated_at": _utc_now(),
        "data_status": "ready" if responses else "insufficient_data",
        "metric_feasibility": metric_feasibility(),
        "metrics": {
            "action_validity": evaluate_action_validity(track),
            "execution_rounds": evaluate_execution_rounds(track),
            "cross_file_retrieval": evaluate_cross_file_retrieval(track),
        },
    }


def evaluate_track_file(
    track_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    source = Path(track_path)
    try:
        track = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid track JSON: {source}") from exc
    result = evaluate_track(track)
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return result
