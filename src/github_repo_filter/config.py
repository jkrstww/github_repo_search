from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "search": {
        "raw_query": "",
        "keywords": [],
        "any_keywords": [],
        "in_fields": [],
        "language": "",
        "topics": [],
        "owner": "",
        "org": "",
        "sort": "stars",
        "order": "desc",
        "per_page": 100,
        "max_results": None,
        "include_forks": False,
        "include_archived": False,
        "created_split": {
            "enabled": False,
            "start": "",
            "end": "",
            "interval": "month",
        },
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


def load_env_value(name: str, path: str | Path = ".env") -> tuple[str | None, str | None]:
    """Read one value from the process environment or a simple dotenv file.

    Existing environment variables take precedence.  This keeps the root project
    dependency-free while supporting the common KEY=value dotenv syntax.
    """
    value = os.environ.get(name)
    if value:
        return value.strip(), "environment"

    env_path = Path(path)
    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return None, None

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        if not separator or key.strip() != name:
            continue
        dotenv_value = raw_value.strip()
        if len(dotenv_value) >= 2 and dotenv_value[0] == dotenv_value[-1] and dotenv_value[0] in {"'", '"'}:
            dotenv_value = dotenv_value[1:-1]
        return (dotenv_value or None), str(env_path)

    return None, None
