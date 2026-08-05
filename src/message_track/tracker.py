from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 2


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _json_value(value: Any) -> Any:
    """Convert raw SDK responses and common Python values to JSON values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _json_value(model_dump(mode="json"))
        except TypeError:
            return _json_value(model_dump())
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _json_value(to_dict())
    if is_dataclass(value):
        return _json_value(asdict(value))
    if hasattr(value, "__dict__"):
        return _json_value(vars(value))
    return str(value)


@dataclass(frozen=True)
class ResponseRecord:
    """One agent response captured during task execution."""

    seq: int
    recorded_at: str
    response_id: str | None
    response: Any
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MessageTrack:
    """Persist the raw responses emitted by an agent for one instance.

    A response is stored as one entry instead of being split into synthetic
    events. This preserves provider output such as reasoning items, assistant
    messages, tool calls, status, model details, and token usage.
    """

    def __init__(
        self,
        instance_dir: str | Path,
        *,
        output_path: str | Path | None = None,
        track_dir: str | Path | None = None,
        instance_id: str | None = None,
        task_metadata: Mapping[str, Any] | None = None,
        resume: bool = True,
    ) -> None:
        self.instance_dir = Path(instance_dir)
        self._lock = threading.RLock()

        metadata = dict(task_metadata or self._load_instance_metadata())
        discovered_id = str(
            instance_id or metadata.get("instance_id") or self.instance_dir.name
        )
        self._validate_instance_name(discovered_id)
        if output_path is not None:
            self.output_path = Path(output_path)
        else:
            root = Path(track_dir) if track_dir is not None else self._default_track_dir()
            self.output_path = root / discovered_id / "trajectory.json"

        existing = self._read_existing() if resume and self.output_path.is_file() else None
        if existing is not None:
            self._document = existing
            existing_id = existing.get("instance_id")
            if existing_id not in (None, discovered_id):
                raise ValueError(
                    f"message track belongs to instance {existing_id!r}, not {discovered_id!r}"
                )
            if self.status != "recording":
                raise ValueError("cannot append to a closed message track")
            return

        now = _utc_now()
        self._document: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "track_id": str(uuid.uuid4()),
            "instance_id": discovered_id,
            "task_type": metadata.get("task_type", "unknown"),
            "task_metadata": _json_value(metadata),
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
            "status": "recording",
            "summary": None,
            "responses": [],
        }
        self._persist()

    @staticmethod
    def _validate_instance_name(instance_name: str) -> None:
        if (
            not instance_name
            or instance_name in {".", ".."}
            or Path(instance_name).name != instance_name
            or "/" in instance_name
            or "\\" in instance_name
        ):
            raise ValueError("instance_id must be a safe directory name")

    def _default_track_dir(self) -> Path:
        for parent in (self.instance_dir, *self.instance_dir.parents):
            if parent.name == "instances":
                return parent.parent / "instance_tracks"
        return Path.cwd() / "instance_tracks"

    def _load_instance_metadata(self) -> dict[str, Any]:
        metadata_path = self.instance_dir / "instance.json"
        if not metadata_path.is_file():
            return {}
        try:
            value = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid instance metadata: {metadata_path}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"instance metadata must be an object: {metadata_path}")
        return value

    def _read_existing(self) -> dict[str, Any]:
        try:
            document = json.loads(self.output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid message track JSON: {self.output_path}") from exc
        if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported message track format: {self.output_path}")
        if not isinstance(document.get("responses"), list):
            raise ValueError(f"message track responses must be a list: {self.output_path}")
        return document

    def _persist(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._document, ensure_ascii=False, indent=2) + "\n"
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.output_path.name}.",
            suffix=".tmp",
            dir=self.output_path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.output_path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    @property
    def track_id(self) -> str:
        return str(self._document["track_id"])

    @property
    def response_count(self) -> int:
        return len(self._document["responses"])

    @property
    def status(self) -> str:
        return str(self._document["status"])

    def record_response(
        self,
        response: Any,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> ResponseRecord:
        """Append one complete agent response and immediately persist it."""
        if response is None:
            raise ValueError("response must not be None")
        with self._lock:
            if self.status != "recording":
                raise ValueError("cannot append to a closed message track")
            payload = _json_value(response)
            response_id = payload.get("id") if isinstance(payload, dict) else None
            timestamp = _utc_now()
            record = ResponseRecord(
                seq=self.response_count + 1,
                recorded_at=timestamp,
                response_id=str(response_id) if response_id is not None else None,
                response=payload,
                metadata=_json_value(dict(metadata or {})),
            )
            self._document["responses"].append(record.to_dict())
            self._document["updated_at"] = timestamp
            self._persist()
            return record

    def close(self, *, status: str = "completed", summary: str | None = None) -> None:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("status must be completed, failed, or cancelled")
        with self._lock:
            if self.status != "recording":
                raise ValueError("message track is already closed")
            timestamp = _utc_now()
            self._document["status"] = status
            self._document["summary"] = summary
            self._document["updated_at"] = timestamp
            self._document["closed_at"] = timestamp
            self._persist()

    def __enter__(self) -> "MessageTrack":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if self.status == "recording":
            self.close(
                status="failed" if exc_type is not None else "completed",
                summary=str(exc_value) if exc_value is not None else None,
            )


def load_message_track(path: str | Path) -> dict[str, Any]:
    """Load a persisted message track without opening it for append."""
    track_path = Path(path)
    try:
        value = json.loads(track_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid message track JSON: {track_path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported message track format: {track_path}")
    if not isinstance(value.get("responses"), list):
        raise ValueError(f"message track responses must be a list: {track_path}")
    return value


# Compatibility aliases for callers that used the initial trajectory names.
TrajectoryTracker = MessageTrack
load_trajectory = load_message_track
