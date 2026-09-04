from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.client import IncompleteRead
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .config import as_list


SEARCH_REPOSITORIES_URL = "https://api.github.com/search/repositories"
DEFAULT_API_VERSION = "2022-11-28"
MAX_SEARCH_RESULTS = 1000
REPOSITORY_SEARCH_FIELDS = {"name", "description", "topics", "readme"}


class GitHubApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchResult:
    repositories: list[dict[str, Any]]
    total_count: int
    incomplete_results: bool
    query: str


@dataclass(frozen=True)
class SearchPage:
    repositories: list[dict[str, Any]]
    total_count: int
    incomplete_results: bool
    query: str
    page: int
    per_page: int
    fetched_count: int
    target_count: int


@dataclass(frozen=True)
class CreatedRange:
    start: date
    end: date

    def qualifier(self) -> str:
        return f"created:{self.start.isoformat()}..{self.end.isoformat()}"


@dataclass(frozen=True)
class PlannedSearchQuery:
    query: str
    created_range: CreatedRange | None = None
    keyword: str | None = None


def build_search_query(
    search: dict[str, Any],
    filters: dict[str, Any] | None = None,
    *,
    created_range: CreatedRange | None = None,
    include_filter_created: bool = True,
    extra_keyword: str | None = None,
) -> str:
    filters = filters or {}
    terms: list[str] = []

    raw_query = str(search.get("raw_query") or "").strip()
    if raw_query:
        terms.append(raw_query)

    for required_keyword in as_list(search.get("keywords")):
        keyword_text = str(required_keyword).strip()
        if keyword_text:
            terms.append(_quote_term(keyword_text))

    if extra_keyword is not None:
        keyword_text = str(extra_keyword).strip()
        if keyword_text:
            terms.append(_quote_term(keyword_text))

    in_qualifier = _in_qualifier(search.get("in_fields"))
    if in_qualifier:
        terms.append(in_qualifier)

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

    if created_range is not None:
        terms.append(created_range.qualifier())
    elif include_filter_created:
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


def build_search_queries(
    search: dict[str, Any],
    filters: dict[str, Any] | None = None,
    *,
    today: date | None = None,
) -> list[PlannedSearchQuery]:
    filters = filters or {}
    keyword_variants = _keyword_variants(search)
    split = search.get("created_split") or {}
    if not bool(split.get("enabled", False)):
        return [
            PlannedSearchQuery(
                build_search_query(search, filters, extra_keyword=keyword),
                keyword=keyword,
            )
            for keyword in keyword_variants
        ]

    start = split.get("start") or filters.get("created_after")
    if not start:
        raise ValueError("search.created_split.start or filters.created_after is required when created_split is enabled")

    end = split.get("end") or filters.get("created_before") or (today or date.today()).isoformat()
    interval = str(split.get("interval") or "month").strip().lower()
    ranges = build_created_ranges(start, end, interval=interval)
    planned: list[PlannedSearchQuery] = []
    for created_range in ranges:
        for keyword in keyword_variants:
            planned.append(
                PlannedSearchQuery(
                    build_search_query(
                        search,
                        filters,
                        created_range=created_range,
                        include_filter_created=False,
                        extra_keyword=keyword,
                    ),
                    created_range=created_range,
                    keyword=keyword,
                )
            )
    return planned


def build_created_ranges(start: Any, end: Any, *, interval: str = "month") -> list[CreatedRange]:
    start_date = _parse_date_boundary(start, boundary="start")
    end_date = _parse_date_boundary(end, boundary="end")
    if end_date < start_date:
        raise ValueError(f"created split end date {end_date} is before start date {start_date}")

    if interval not in {"day", "month", "year"}:
        raise ValueError("created split interval must be one of: day, month, year")

    ranges: list[CreatedRange] = []
    cursor = start_date
    while cursor <= end_date:
        if interval == "day":
            range_end = cursor
        elif interval == "month":
            range_end = _month_end(cursor)
        else:
            range_end = date(cursor.year, 12, 31)

        range_end = min(range_end, end_date)
        ranges.append(CreatedRange(start=cursor, end=range_end))
        cursor = range_end + timedelta(days=1)

    return ranges


def search_repositories(
    query: str,
    *,
    token: str | None = None,
    sort: str = "stars",
    order: str = "desc",
    per_page: int = 100,
    max_results: int | None = None,
    timeout: int = 30,
    pause_seconds: float = 0.0,
) -> SearchResult:
    repositories: list[dict[str, Any]] = []
    total_count = 0
    incomplete_results = False

    for page in iter_search_repositories(
        query,
        token=token,
        sort=sort,
        order=order,
        per_page=per_page,
        max_results=max_results,
        timeout=timeout,
        pause_seconds=pause_seconds,
    ):
        repositories.extend(page.repositories)
        total_count = page.total_count
        incomplete_results = page.incomplete_results

    return SearchResult(
        repositories=repositories,
        total_count=total_count,
        incomplete_results=incomplete_results,
        query=query,
    )


def iter_search_repositories(
    query: str,
    *,
    token: str | None = None,
    sort: str = "stars",
    order: str = "desc",
    per_page: int = 100,
    max_results: int | None = None,
    timeout: int = 30,
    pause_seconds: float = 0.0,
) -> Iterator[SearchPage]:
    if not query.strip():
        raise ValueError("GitHub repository search query cannot be empty")

    per_page = max(1, min(int(per_page), 100))
    requested_limit = _normalise_max_results(max_results)
    fetched_count = 0
    page = 1

    while True:
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

        target_count = min(total_count, requested_limit)
        remaining_slots = max(0, target_count - fetched_count)
        page_items = items[:remaining_slots]
        fetched_count += len(page_items)

        yield SearchPage(
            repositories=page_items,
            total_count=total_count,
            incomplete_results=incomplete_results,
            query=query,
            page=page,
            per_page=per_page,
            fetched_count=fetched_count,
            target_count=target_count,
        )

        if fetched_count >= target_count or len(items) < per_page:
            break
        if pause_seconds:
            time.sleep(pause_seconds)
        page += 1


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
    attempts = 5
    last_error: Exception | None = None
    attempt = 1
    while attempt <= attempts:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (IncompleteRead, TimeoutError, ConnectionError, ssl.SSLError) as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(min(2**attempt, 8))
            attempt += 1
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            message = _extract_error_message(body) or exc.reason
            reset = exc.headers.get("X-RateLimit-Reset")
            wait_seconds = _rate_limit_wait_seconds(exc, message)
            if wait_seconds is not None:
                reset_message = f", reset at {_format_epoch(reset)}" if reset else ""
                print(
                    f"GitHub API rate limit reached{reset_message}; "
                    f"waiting {wait_seconds:.0f} seconds before retrying",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(wait_seconds)
                continue
            raise GitHubApiError(f"GitHub API error {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(min(2**attempt, 8))
            attempt += 1
        except json.JSONDecodeError as exc:
            raise GitHubApiError("GitHub API returned invalid JSON") from exc

    if isinstance(last_error, urllib.error.URLError):
        raise GitHubApiError(f"failed to call GitHub API: {last_error.reason}") from last_error
    raise GitHubApiError(f"failed to read GitHub API response after retries: {last_error}") from last_error


def _extract_error_message(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body.strip()
    return str(payload.get("message") or "").strip()


def _rate_limit_wait_seconds(exc: urllib.error.HTTPError, message: str) -> float | None:
    if exc.code not in {403, 429}:
        return None

    remaining = exc.headers.get("X-RateLimit-Remaining")
    is_rate_limit = remaining == "0" or "rate limit" in message.casefold() or exc.code == 429
    if not is_rate_limit:
        return None

    retry_after = exc.headers.get("Retry-After")
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            pass

    reset = exc.headers.get("X-RateLimit-Reset")
    if reset:
        try:
            return max(1.0, int(reset) - time.time() + 1.0)
        except (TypeError, ValueError):
            pass

    # GitHub secondary limits do not always include a reset timestamp.
    return 60.0


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


def _in_qualifier(value: Any) -> str:
    fields = []
    for field in as_list(value):
        field_text = str(field).strip().casefold()
        if not field_text:
            continue
        if field_text not in REPOSITORY_SEARCH_FIELDS:
            raise ValueError(
                "repository search in_fields must only contain: "
                f"{', '.join(sorted(REPOSITORY_SEARCH_FIELDS))}"
            )
        fields.append(field_text)
    if not fields:
        return ""
    unique_fields = list(dict.fromkeys(fields))
    return f"in:{','.join(unique_fields)}"


def _keyword_variants(search: dict[str, Any]) -> list[str | None]:
    variants = [str(keyword).strip() for keyword in as_list(search.get("any_keywords")) if str(keyword).strip()]
    unique_variants = list(dict.fromkeys(variants))
    return unique_variants or [None]


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


def _normalise_max_results(value: Any) -> int:
    if value in (None, "", "all"):
        return MAX_SEARCH_RESULTS
    return max(1, min(int(value), MAX_SEARCH_RESULTS))


def _parse_date_boundary(value: Any, *, boundary: str) -> date:
    if value in (None, ""):
        raise ValueError("date value cannot be empty")
    text = str(value).strip()
    if len(text) == 7:
        year, month = map(int, text.split("-", 1))
        parsed = date(year, month, 1)
        if boundary == "end":
            return _month_end(parsed)
        return parsed
    if len(text) >= 10:
        return date.fromisoformat(text[:10])
    raise ValueError(f"date must be YYYY-MM or YYYY-MM-DD: {text}")


def _month_end(value: date) -> date:
    if value.month == 12:
        return date(value.year, 12, 31)
    return date(value.year, value.month + 1, 1) - timedelta(days=1)
