from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import as_list


def filter_repositories(
    repositories: list[dict[str, Any]],
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    return [repo for repo in repositories if not rejection_reasons(repo, filters)]


def rejection_reasons(repo: dict[str, Any], filters: dict[str, Any]) -> list[str]:
    reasons: list[str] = []

    if filters.get("exclude_archived", True) and repo.get("archived"):
        reasons.append("archived")

    if filters.get("exclude_forks", True) and repo.get("fork"):
        reasons.append("fork")

    reasons.extend(_count_rejections(repo, filters))
    reasons.extend(_date_rejections(repo, filters))
    reasons.extend(_list_rejections(repo, filters))

    return reasons


def _count_rejections(repo: dict[str, Any], filters: dict[str, Any]) -> list[str]:
    reasons: list[str] = []

    stars = int(repo.get("stargazers_count") or 0)
    forks = int(repo.get("forks_count") or 0)

    min_stars = filters.get("min_stars")
    max_stars = filters.get("max_stars")
    min_forks = filters.get("min_forks")
    max_forks = filters.get("max_forks")

    if min_stars is not None and stars < int(min_stars):
        reasons.append("stars_below_min")
    if max_stars is not None and stars > int(max_stars):
        reasons.append("stars_above_max")
    if min_forks is not None and forks < int(min_forks):
        reasons.append("forks_below_min")
    if max_forks is not None and forks > int(max_forks):
        reasons.append("forks_above_max")

    return reasons


def _date_rejections(repo: dict[str, Any], filters: dict[str, Any]) -> list[str]:
    checks = [
        ("created_at", "created_after", "created_before", "created_out_of_range"),
        ("updated_at", "updated_after", "updated_before", "updated_out_of_range"),
        ("pushed_at", "pushed_after", "pushed_before", "pushed_out_of_range"),
    ]

    reasons: list[str] = []
    for repo_field, after_key, before_key, reason in checks:
        repo_dt = parse_datetime(repo.get(repo_field))
        after = parse_datetime(filters.get(after_key))
        before = parse_datetime(filters.get(before_key))
        if after is not None and (repo_dt is None or repo_dt < after):
            reasons.append(reason)
        if before is not None and (repo_dt is None or repo_dt > before):
            reasons.append(reason)

    return reasons


def _list_rejections(repo: dict[str, Any], filters: dict[str, Any]) -> list[str]:
    reasons: list[str] = []

    languages = _normalised_values(filters.get("languages"))
    repo_language = str(repo.get("language") or "").casefold()
    if languages and repo_language not in languages:
        reasons.append("language_not_allowed")

    owners = _normalised_values(filters.get("owners"))
    repo_owner = str((repo.get("owner") or {}).get("login") or "").casefold()
    if owners and repo_owner not in owners:
        reasons.append("owner_not_allowed")

    required_topics = _normalised_values(filters.get("has_topics"))
    repo_topics = _normalised_values(repo.get("topics"))
    if required_topics and not required_topics.issubset(repo_topics):
        reasons.append("missing_topic")

    allowed_licenses = _normalised_values(filters.get("license"))
    repo_license = _repo_license(repo).casefold()
    if allowed_licenses and repo_license not in allowed_licenses:
        reasons.append("license_not_allowed")

    return reasons


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None

    text = str(value).strip()
    if not text:
        return None

    if len(text) == 10:
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)

    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"

    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalised_values(value: Any) -> set[str]:
    return {str(item).strip().casefold() for item in as_list(value) if str(item).strip()}


def _repo_license(repo: dict[str, Any]) -> str:
    license_info = repo.get("license") or {}
    for key in ("spdx_id", "key", "name"):
        value = license_info.get(key)
        if value:
            return str(value)
    return ""
