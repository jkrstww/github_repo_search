from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "search": {
        "raw_query": "",
        "keywords": [],
        "language": "",
        "topics": [],
        "owner": "",
        "org": "",
        "sort": "stars",
        "order": "desc",
        "per_page": 100,
        "max_results": 100,
        "include_forks": False,
        "include_archived": False,
    },
    "filters": {
        "created_after": "",
        "created_before": "",
        "updated_after": "",
        "updated_before": "",
        "pushed_after": "",
        "pushed_before": "",
        "min_stars": None,
        "max_stars": None,
        "min_forks": None,
        "max_forks": None,
        "languages": [],
        "owners": [],
        "has_topics": [],
        "license": [],
        "exclude_archived": True,
        "exclude_forks": True,
    },
    "output": {
        "path": "data/repositories.jsonl",
        "dedupe": True,
    },
}


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    if path is None:
        return config

    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as fp:
        user_config = json.load(fp)

    if not isinstance(user_config, dict):
        raise ValueError(f"config must be a JSON object: {config_path}")

    return deep_merge(config, user_config)


def deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]
