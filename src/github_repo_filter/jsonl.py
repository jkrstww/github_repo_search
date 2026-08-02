from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WriteSummary:
    path: Path
    inserted: int
    updated: int
    total: int


def repository_to_record(
    repo: dict[str, Any],
    *,
    source_query: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    owner = repo.get("owner") or {}
    license_info = repo.get("license") or {}
    full_name = str(repo.get("full_name") or "")
    repo_owner, _, repo_name = full_name.partition("/")

    return {
        "full_name": full_name,
        "owner": owner.get("login") or repo_owner,
        "repo": repo.get("name") or repo_name,
        "html_url": repo.get("html_url"),
        "description": repo.get("description"),
        "language": repo.get("language"),
        "stargazers_count": repo.get("stargazers_count"),
        "forks_count": repo.get("forks_count"),
        "open_issues_count": repo.get("open_issues_count"),
        "license": {
            "key": license_info.get("key"),
            "spdx_id": license_info.get("spdx_id"),
            "name": license_info.get("name"),
        }
        if license_info
        else None,
        "topics": repo.get("topics") or [],
        "created_at": repo.get("created_at"),
        "updated_at": repo.get("updated_at"),
        "pushed_at": repo.get("pushed_at"),
        "archived": repo.get("archived"),
        "fork": repo.get("fork"),
        "default_branch": repo.get("default_branch"),
        "size": repo.get("size"),
        "source_query": source_query,
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
    }


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    jsonl_path = Path(path)
    if not jsonl_path.exists():
        return []

    records: list[dict[str, Any]] = []
    with jsonl_path.open("r", encoding="utf-8") as fp:
        for line_number, line in enumerate(fp, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {jsonl_path}:{line_number}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"JSONL record must be an object at {jsonl_path}:{line_number}")
            records.append(record)
    return records


def write_jsonl(
    path: str | Path,
    records: list[dict[str, Any]],
    *,
    dedupe: bool = True,
    key: str = "full_name",
) -> WriteSummary:
    jsonl_path = Path(path)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    if not dedupe:
        with jsonl_path.open("a", encoding="utf-8") as fp:
            for record in records:
                fp.write(_dumps(record))
                fp.write("\n")
        total = len(load_jsonl(jsonl_path))
        return WriteSummary(path=jsonl_path, inserted=len(records), updated=0, total=total)

    existing_records = load_jsonl(jsonl_path)
    merged = list(existing_records)
    index = {str(record.get(key) or ""): idx for idx, record in enumerate(merged) if record.get(key)}
    inserted = 0
    updated = 0

    for record in records:
        record_key = str(record.get(key) or "")
        if not record_key:
            inserted += 1
            merged.append(record)
            continue
        if record_key in index:
            merged[index[record_key]] = record
            updated += 1
        else:
            index[record_key] = len(merged)
            merged.append(record)
            inserted += 1

    with jsonl_path.open("w", encoding="utf-8") as fp:
        for record in merged:
            fp.write(_dumps(record))
            fp.write("\n")

    return WriteSummary(path=jsonl_path, inserted=inserted, updated=updated, total=len(merged))


def overwrite_jsonl(path: str | Path, records: list[dict[str, Any]]) -> WriteSummary:
    jsonl_path = Path(path)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    with jsonl_path.open("w", encoding="utf-8") as fp:
        for record in records:
            fp.write(_dumps(record))
            fp.write("\n")

    return WriteSummary(path=jsonl_path, inserted=len(records), updated=0, total=len(records))


def _dumps(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
