from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.client import IncompleteRead
from typing import Any, Iterable

from .github import DEFAULT_API_VERSION, GitHubApiError


GITHUB_API_URL = "https://api.github.com"
CONFIDENCE_LEVELS = {"none": 0, "low": 1, "medium": 2, "high": 3}
METADATA_KEYWORDS = (
    "arkts",
    "arkui",
    "harmonyos",
    "harmony os",
    "openharmony",
    "open harmony",
    "ohos",
    "鸿蒙",
)


@dataclass(frozen=True)
class HarmonyEvidence:
    confidence: str
    accepted: bool
    ets_file_count: int
    build_markers: tuple[str, ...]
    metadata_keywords: tuple[str, ...]
    tree_truncated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence,
            "accepted": self.accepted,
            "ets_file_count": self.ets_file_count,
            "build_markers": list(self.build_markers),
            "metadata_keywords": list(self.metadata_keywords),
            "tree_truncated": self.tree_truncated,
        }


def classify_harmony_repository(
    record: dict[str, Any],
    paths: Iterable[str],
    *,
    tree_truncated: bool = False,
    min_confidence: str = "medium",
) -> HarmonyEvidence:
    if min_confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"unknown confidence level: {min_confidence}")

    normalized_paths = [str(path).strip().casefold() for path in paths if str(path).strip()]
    ets_file_count = sum(path.endswith(".ets") for path in normalized_paths)
    build_markers = tuple(
        name
        for name, predicate in _build_marker_predicates()
        if any(predicate(path) for path in normalized_paths)
    )
    metadata_keywords = _metadata_keywords(record)
    marker_count = len(build_markers)

    if ets_file_count > 0 and marker_count >= 2:
        confidence = "high"
    elif (
        ets_file_count > 0
        and marker_count >= 1
        and metadata_keywords
    ) or (marker_count >= 3 and metadata_keywords):
        confidence = "medium"
    elif ets_file_count > 0 and metadata_keywords:
        confidence = "low"
    else:
        confidence = "none"

    return HarmonyEvidence(
        confidence=confidence,
        accepted=CONFIDENCE_LEVELS[confidence] >= CONFIDENCE_LEVELS[min_confidence],
        ets_file_count=ets_file_count,
        build_markers=build_markers,
        metadata_keywords=metadata_keywords,
        tree_truncated=tree_truncated,
    )


def fetch_repository_tree(
    record: dict[str, Any],
    *,
    token: str | None,
    timeout: int = 60,
) -> tuple[list[str], bool]:
    full_name = str(record.get("full_name") or "").strip()
    default_branch = str(record.get("default_branch") or "").strip()
    if not full_name or "/" not in full_name:
        raise ValueError("repository record must contain full_name as owner/repo")
    if not default_branch:
        raise ValueError(f"repository record is missing default_branch: {full_name}")

    encoded_repo = "/".join(urllib.parse.quote(part, safe="") for part in full_name.split("/", 1))
    encoded_branch = urllib.parse.quote(default_branch, safe="")
    url = f"{GITHUB_API_URL}/repos/{encoded_repo}/git/trees/{encoded_branch}?recursive=1"
    payload = _request_json(url, token=token, timeout=timeout)
    paths = [
        str(item.get("path") or "")
        for item in payload.get("tree") or []
        if item.get("type") == "blob" and item.get("path")
    ]
    return paths, bool(payload.get("truncated"))


def _metadata_keywords(record: dict[str, Any]) -> tuple[str, ...]:
    text_parts = [
        record.get("full_name"),
        record.get("description"),
        *(record.get("topics") or []),
    ]
    text = " ".join(str(part) for part in text_parts if part).casefold()
    return tuple(keyword for keyword in METADATA_KEYWORDS if keyword in text)


def _build_marker_predicates():
    return (
        ("build-profile.json5", lambda path: path == "build-profile.json5" or path.endswith("/build-profile.json5")),
        ("hvigorfile.ts", lambda path: path == "hvigorfile.ts" or path.endswith("/hvigorfile.ts")),
        ("oh-package.json5", lambda path: path == "oh-package.json5" or path.endswith("/oh-package.json5")),
        ("AppScope/app.json5", lambda path: path.endswith("appscope/app.json5")),
        ("module.json5", lambda path: path == "module.json5" or path.endswith("/module.json5")),
        ("hvigor-config.json5", lambda path: path.endswith("hvigor-config.json5")),
    )


def _request_json(url: str, *, token: str | None, timeout: int) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": DEFAULT_API_VERSION,
        "User-Agent": "github-repo-filter",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    attempts = 5
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (IncompleteRead, TimeoutError, ConnectionError, ssl.SSLError) as exc:
            last_error = exc
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                message = str(json.loads(body).get("message") or exc.reason)
            except json.JSONDecodeError:
                message = body.strip() or str(exc.reason)
            raise GitHubApiError(f"GitHub API error {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            last_error = exc
        except json.JSONDecodeError as exc:
            raise GitHubApiError("GitHub API returned invalid JSON") from exc

        if attempt < attempts:
            time.sleep(min(2**attempt, 8))

    if isinstance(last_error, urllib.error.URLError):
        raise GitHubApiError(f"failed to call GitHub API: {last_error.reason}") from last_error
    raise GitHubApiError(f"failed to read GitHub API response after retries: {last_error}") from last_error
