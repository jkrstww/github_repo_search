from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from .config import as_list, load_config
from .filters import filter_repositories
from .github import GitHubApiError, build_search_query, search_repositories
from .jsonl import repository_to_record, write_jsonl


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        _apply_overrides(config, args)
        search = config["search"]
        filters = config["filters"]
        output = config["output"]
        query = build_search_query(search, filters)

        if not query:
            parser.error("provide --keyword, --raw-query, or matching values in --config")

        if args.show_query or args.dry_run:
            print(query)
        if args.dry_run:
            return 0

        token = os.environ.get(args.token_env)
        result = search_repositories(
            query,
            token=token,
            sort=search.get("sort") or "stars",
            order=search.get("order") or "desc",
            per_page=int(search.get("per_page") or 100),
            max_results=int(search.get("max_results") or 100),
            timeout=int(args.timeout),
            pause_seconds=float(args.pause),
        )

        matched = filter_repositories(result.repositories, filters)
        records = [repository_to_record(repo, source_query=result.query) for repo in matched]

        if not args.no_write:
            summary = write_jsonl(
                output.get("path") or "data/repositories.jsonl",
                records,
                dedupe=bool(output.get("dedupe", True)),
            )
            print(
                "saved "
                f"{len(records)} records to {summary.path} "
                f"(inserted={summary.inserted}, updated={summary.updated}, total={summary.total})",
                file=sys.stderr,
            )
        else:
            print(f"matched {len(records)} records; no file written", file=sys.stderr)

        print(
            "github search "
            f"returned={len(result.repositories)} total_count={result.total_count} "
            f"incomplete={result.incomplete_results}",
            file=sys.stderr,
        )
        for record in records:
            print(record["full_name"])
        return 0
    except (GitHubApiError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="github-repo-filter",
        description="Search GitHub repositories, filter them locally, and persist matches as JSONL.",
    )
    parser.add_argument("--config", type=Path, help="JSON config file path")
    parser.add_argument("--raw-query", help="raw GitHub repository search query")
    parser.add_argument("--keyword", action="append", help="keyword to search; can be repeated")
    parser.add_argument("--language", help="primary repository language qualifier")
    parser.add_argument("--topic", action="append", help="GitHub topic qualifier; can be repeated")
    parser.add_argument("--owner", help="GitHub user owner qualifier")
    parser.add_argument("--org", help="GitHub org qualifier")
    parser.add_argument("--sort", choices=["stars", "forks", "help-wanted-issues", "updated"], help="GitHub sort key")
    parser.add_argument("--order", choices=["asc", "desc"], help="GitHub sort order")
    parser.add_argument("--max-results", type=int, help="maximum repositories to fetch, capped by GitHub at 1000")
    parser.add_argument("--per-page", type=int, help="GitHub search page size, 1-100")
    parser.add_argument("--min-stars", type=int, help="minimum stargazers_count")
    parser.add_argument("--max-stars", type=int, help="maximum stargazers_count")
    parser.add_argument("--created-after", help="include repos created on or after YYYY-MM-DD")
    parser.add_argument("--created-before", help="include repos created on or before YYYY-MM-DD")
    parser.add_argument("--updated-after", help="include repos updated on or after YYYY-MM-DD")
    parser.add_argument("--updated-before", help="include repos updated on or before YYYY-MM-DD")
    parser.add_argument("--pushed-after", help="include repos pushed on or after YYYY-MM-DD")
    parser.add_argument("--pushed-before", help="include repos pushed on or before YYYY-MM-DD")
    parser.add_argument("--min-forks", type=int, help="minimum forks_count")
    parser.add_argument("--max-forks", type=int, help="maximum forks_count")
    parser.add_argument("--allow-language", action="append", help="allowed local-filter language; can be repeated")
    parser.add_argument("--allow-owner", action="append", help="allowed local-filter owner; can be repeated")
    parser.add_argument("--has-topic", action="append", help="required topic after local filtering; can be repeated")
    parser.add_argument("--license", action="append", help="allowed license key, SPDX id, or name; can be repeated")
    parser.add_argument("--include-forks", action="store_true", help="include forks in search and local filtering")
    parser.add_argument("--include-archived", action="store_true", help="include archived repos in search and local filtering")
    parser.add_argument("--output", help="JSONL output path")
    parser.add_argument("--no-dedupe", action="store_true", help="append records without replacing existing full_name entries")
    parser.add_argument("--no-write", action="store_true", help="do not write JSONL")
    parser.add_argument("--show-query", action="store_true", help="print the final GitHub search query")
    parser.add_argument("--dry-run", action="store_true", help="print the query and exit without calling GitHub")
    parser.add_argument("--token-env", default="GITHUB_TOKEN", help="environment variable containing a GitHub token")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds to pause between GitHub API pages")
    return parser


def _apply_overrides(config: dict[str, Any], args: argparse.Namespace) -> None:
    search = config["search"]
    filters = config["filters"]
    output = config["output"]

    _set_if_present(search, "raw_query", args.raw_query)
    _set_if_present(search, "keywords", args.keyword)
    _set_if_present(search, "language", args.language)
    _set_if_present(search, "topics", args.topic)
    _set_if_present(search, "owner", args.owner)
    _set_if_present(search, "org", args.org)
    _set_if_present(search, "sort", args.sort)
    _set_if_present(search, "order", args.order)
    _set_if_present(search, "max_results", args.max_results)
    _set_if_present(search, "per_page", args.per_page)

    _set_if_present(filters, "min_stars", args.min_stars)
    _set_if_present(filters, "max_stars", args.max_stars)
    _set_if_present(filters, "created_after", args.created_after)
    _set_if_present(filters, "created_before", args.created_before)
    _set_if_present(filters, "updated_after", args.updated_after)
    _set_if_present(filters, "updated_before", args.updated_before)
    _set_if_present(filters, "pushed_after", args.pushed_after)
    _set_if_present(filters, "pushed_before", args.pushed_before)
    _set_if_present(filters, "min_forks", args.min_forks)
    _set_if_present(filters, "max_forks", args.max_forks)
    _set_if_present(filters, "languages", args.allow_language)
    _set_if_present(filters, "owners", args.allow_owner)
    _set_if_present(filters, "has_topics", args.has_topic)
    _set_if_present(filters, "license", args.license)
    _set_if_present(output, "path", args.output)

    if args.include_forks:
        search["include_forks"] = True
        filters["exclude_forks"] = False
    if args.include_archived:
        search["include_archived"] = True
        filters["exclude_archived"] = False
    if args.no_dedupe:
        output["dedupe"] = False

    if not as_list(filters.get("languages")) and search.get("language"):
        filters["languages"] = [search["language"]]


def _set_if_present(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value
