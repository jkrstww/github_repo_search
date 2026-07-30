from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import as_list


SEARCH_REPOSITORIES_URL = "https://api.github.com/search/repositories"
DEFAULT_API_VERSION = "2022-11-28"


class GitHubApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchResult:
    repositories: list[dict[str, Any]]
    total_count: int
    incomplete_results: bool
    query: str


def build_search_query(search: dict[str, Any], filters: dict[str, Any] | None = None) -> str:
    filters = filters or {}
    terms: list[str] = []

    raw_query = str(search.get("raw_query") or "").strip()
    if raw_query:
        terms.append(raw_query)

    for keyword in as_list(search.get("keywords")):
        keyword_text = str(keyword).strip()
        if keyword_text:
            terms.append(_quote_term(keyword_text))

    language = str(search.get("language") or "").strip()
    if language:
        terms.append(f"language:{_quote_qualifier_value(language)}")

    for topic in as_list(search.get("topics")):
        topic_text = str(topic).strip()
        if topic_text:
            terms.append(f"topic:{_quote_qualifier_value(topic_text)}")

    owner = str(search.get("owner") or "").strip()
    if owner:
        terms.append(f"user:{_quote_qualifier_value(owner)}")

    org = str(search.get("org") or "").strip()
    if org:
        terms.append(f"org:{_quote_qualifier_value(org)}")

    if not bool(search.get("include_forks", False)):
        terms.append("fork:false")

    if not bool(search.get("include_archived", False)):
        terms.append("archived:false")

    stars = _range_qualifier("stars", filters.get("min_stars"), filters.get("max_stars"))
    if stars:
        terms.append(stars)

    created = _range_qualifier(
        "created",
        _date_only(filters.get("created_after")),
        _date_only(filters.get("created_before")),
    )
    if created:
        terms.append(created)

    pushed = _range_qualifier(
        "pushed",
        _date_only(filters.get("pushed_after")),
        _date_only(filters.get("pushed_before")),
    )
    if pushed:
        terms.append(pushed)

    return " ".join(terms)


def search_repositories(
    query: str,
    *,
    token: str | None = None,
    sort: str = "stars",
    order: str = "desc",
    per_page: int = 100,
    max_results: int = 100,
    timeout: int = 30,
    pause_seconds: float = 0.0,
) -> SearchResult:
    if not query.strip():
        raise ValueError("GitHub repository search query cannot be empty")

    per_page = max(1, min(int(per_page), 100))
    max_results = max(1, min(int(max_results), 1000))
    pages = (max_results + per_page - 1) // per_page
    repositories: list[dict[str, Any]] = []
    total_count = 0
    incomplete_results = False

    for page in range(1, pages + 1):
        params = {
            "q": query,
            "sort": sort,
            "order": order,
            "per_page": str(per_page),
            "page": str(page),
        }
        payload = _request_json(params, token=token, timeout=timeout)
        total_count = int(payload.get("total_count") or 0)
        incomplete_results = bool(payload.get("incomplete_results"))
        items = payload.get("items") or []
        if not isinstance(items, list):
            raise GitHubApiError("GitHub search response did not contain an item list")

        remaining_slots = max_results - len(repositories)
        repositories.extend(items[:remaining_slots])
        if len(repositories) >= max_results or len(items) < per_page:
            break
        if pause_seconds:
            time.sleep(pause_seconds)

    return SearchResult(
        repositories=repositories,
        total_count=total_count,
        incomplete_results=incomplete_results,
        query=query,
    )


def _request_json(
    params: dict[str, str],
    *,
    token: str | None,
    timeout: int,
) -> dict[str, Any]:
    url = f"{SEARCH_REPOSITORIES_URL}?{urllib.parse.urlencode(params)}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": DEFAULT_API_VERSION,
        "User-Agent": "github-repo-filter",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        message = _extract_error_message(body) or exc.reason
        reset = exc.headers.get("X-RateLimit-Reset")
        if exc.code == 403 and reset:
            message = f"{message}; rate limit resets at {_format_epoch(reset)}"
        raise GitHubApiError(f"GitHub API error {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise GitHubApiError(f"failed to call GitHub API: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise GitHubApiError("GitHub API returned invalid JSON") from exc


def _extract_error_message(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body.strip()
    return str(payload.get("message") or "").strip()


def _range_qualifier(field: str, lower: Any, upper: Any) -> str:
    if lower in (None, "") and upper in (None, ""):
        return ""
    if lower not in (None, "") and upper not in (None, ""):
        return f"{field}:{lower}..{upper}"
    if lower not in (None, ""):
        return f"{field}:>={lower}"
    return f"{field}:<={upper}"


def _date_only(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if len(text) >= 10:
        return text[:10]
    return text


def _quote_term(value: str) -> str:
    if value.startswith('"') and value.endswith('"'):
        return value
    if any(char.isspace() for char in value):
        return f'"{value}"'
    return value


def _quote_qualifier_value(value: str) -> str:
    if value.startswith('"') and value.endswith('"'):
        return value
    if any(char.isspace() for char in value):
        return f'"{value}"'
    return value


def _format_epoch(value: str) -> str:
    try:
        return datetime.fromtimestamp(int(value)).isoformat()
    except (TypeError, ValueError, OSError):
        return value
