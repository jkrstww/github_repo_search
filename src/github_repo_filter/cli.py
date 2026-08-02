from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .config import as_list, load_config
from .filters import filter_repositories, rejection_reasons
from .github import MAX_SEARCH_RESULTS, GitHubApiError, SearchPage, build_search_queries, iter_search_repositories
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
        planned_queries = build_search_queries(search, filters)

        if not planned_queries or not any(plan.query for plan in planned_queries):
            parser.error("provide --keyword, --raw-query, or matching values in --config")

        if args.show_query or args.dry_run:
            _print_planned_queries(planned_queries)
        if args.dry_run:
            return 0

        token = os.environ.get(args.token_env)
        state = RunState()
        progress = ProgressBar(disabled=args.no_progress)
        total_limit = _global_max_results(search.get("max_results"))
        fetching_all = total_limit is None
        for query_index, planned in enumerate(planned_queries, start=1):
            if args.start_query_index and query_index < args.start_query_index:
                continue
            if args.end_query_index and query_index > args.end_query_index:
                break
            remaining = None if total_limit is None else total_limit - state.fetched
            if remaining is not None and remaining <= 0:
                break
            for page in iter_search_repositories(
                planned.query,
                token=token,
                sort=search.get("sort") or "stars",
                order=search.get("order") or "desc",
                per_page=int(search.get("per_page") or 100),
                max_results=remaining,
                timeout=int(args.timeout),
                pause_seconds=float(args.pause),
            ):
                _handle_page(
                    page,
                    filters=filters,
                    output=output,
                    args=args,
                    state=state,
                    progress=progress,
                    query_index=query_index,
                    total_queries=len(planned_queries),
                    warn_inaccessible=fetching_all,
                )
            if float(args.pause) and query_index < len(planned_queries):
                time.sleep(float(args.pause))
        progress.finish()
        if state.matched == 0 and state.fetched > 0:
            _print_rejection_summary(state.rejection_counts)

        print(
            "github search "
            f"fetched={state.fetched} matched={state.matched} "
            f"inserted={state.inserted} updated={state.updated} "
            f"total_count={state.total_count} incomplete={state.incomplete_results}",
            file=sys.stderr,
        )
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
    parser.add_argument("--any-keyword", action="append", help="alternative keyword to search with OR semantics; can be repeated")
    parser.add_argument("--language", help="primary repository language qualifier")
    parser.add_argument("--topic", action="append", help="GitHub topic qualifier; can be repeated")
    parser.add_argument(
        "--in-field",
        action="append",
        choices=["name", "description", "topics", "readme"],
        help="repository fields searched by keywords; can be repeated",
    )
    parser.add_argument("--owner", help="GitHub user owner qualifier")
    parser.add_argument("--org", help="GitHub org qualifier")
    parser.add_argument("--sort", choices=["stars", "forks", "help-wanted-issues", "updated"], help="GitHub sort key")
    parser.add_argument("--order", choices=["asc", "desc"], help="GitHub sort order")
    parser.add_argument("--max-results", type=int, help="maximum repositories to fetch; default is all available results, capped by GitHub at 1000")
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
    parser.add_argument("--no-progress", action="store_true", help="disable the progress bar")
    parser.add_argument("--quiet", action="store_true", help="do not print matched repository full names")
    parser.add_argument("--start-query-index", type=int, default=0, help="skip planned queries before this 1-based index")
    parser.add_argument("--end-query-index", type=int, default=0, help="stop after this 1-based planned query index")
    parser.add_argument(
        "--explain-filtered",
        type=int,
        default=0,
        metavar="N",
        help="print rejection reasons for the first N repositories returned by GitHub",
    )
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
    _set_if_present(search, "any_keywords", args.any_keyword)
    _set_if_present(search, "language", args.language)
    _set_if_present(search, "topics", args.topic)
    _set_if_present(search, "in_fields", args.in_field)
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


class RunState:
    def __init__(self) -> None:
        self.fetched = 0
        self.matched = 0
        self.inserted = 0
        self.updated = 0
        self.total_count = 0
        self.incomplete_results = False
        self._seen_queries = 0
        self.rejection_counts: Counter[str] = Counter()
        self._warned_queries: set[int] = set()
        self._explained = 0


class ProgressBar:
    def __init__(self, *, disabled: bool = False, width: int = 28) -> None:
        self.disabled = disabled
        self.width = width

    def update(
        self,
        *,
        query_index: int,
        total_queries: int,
        query_fetched: int,
        query_target: int,
        fetched: int,
        matched: int,
        inserted: int,
        updated: int,
    ) -> None:
        if self.disabled:
            return
        if query_target <= 0:
            percent = 100.0
            filled = self.width
        else:
            percent = min(100.0, query_fetched / query_target * 100)
            filled = min(self.width, int(self.width * query_fetched / query_target))
        bar = "#" * filled + "-" * (self.width - filled)
        print(
            f"\r[{bar}] {percent:6.2f}% query={query_index}/{total_queries} "
            f"query_fetched={query_fetched}/{query_target} fetched={fetched} "
            f"matched={matched} inserted={inserted} updated={updated}",
            end="",
            file=sys.stderr,
            flush=True,
        )

    def finish(self) -> None:
        if not self.disabled:
            print(file=sys.stderr)


def _handle_page(
    page: SearchPage,
    *,
    filters: dict[str, Any],
    output: dict[str, Any],
    args: argparse.Namespace,
    state: RunState,
    progress: ProgressBar,
    query_index: int = 1,
    total_queries: int = 1,
    warn_inaccessible: bool = False,
) -> None:
    state.fetched += len(page.repositories)
    if page.page == 1:
        state._seen_queries += 1
        state.total_count += page.total_count
        if (
            warn_inaccessible
            and page.total_count > MAX_SEARCH_RESULTS
            and query_index not in state._warned_queries
        ):
            state._warned_queries.add(query_index)
            print(
                "warning: "
                f"query {query_index}/{total_queries} has total_count={page.total_count}, "
                f"but GitHub Search API exposes at most {MAX_SEARCH_RESULTS} results per query; "
                "use a smaller created_split interval, such as day",
                file=sys.stderr,
            )
    state.incomplete_results = state.incomplete_results or page.incomplete_results

    matched = filter_repositories(page.repositories, filters)
    state.matched += len(matched)
    for repo in page.repositories:
        state.rejection_counts.update(rejection_reasons(repo, filters))

    if args.explain_filtered and state._explained < args.explain_filtered:
        remaining = args.explain_filtered - state._explained
        explained = _print_filter_explanations(page.repositories, filters, limit=remaining)
        state._explained += explained

    if matched:
        records = [repository_to_record(repo, source_query=page.query) for repo in matched]
        if not args.no_write:
            summary = write_jsonl(
                output.get("path") or "data/repositories.jsonl",
                records,
                dedupe=bool(output.get("dedupe", True)),
            )
            state.inserted += summary.inserted
            state.updated += summary.updated
        if not getattr(args, "quiet", False):
            for record in records:
                print(record["full_name"])

    progress.update(
        query_index=query_index,
        total_queries=total_queries,
        query_fetched=page.fetched_count,
        query_target=page.target_count,
        fetched=state.fetched,
        matched=state.matched,
        inserted=state.inserted,
        updated=state.updated,
    )


def _print_filter_explanations(
    repositories: list[dict[str, Any]],
    filters: dict[str, Any],
    *,
    limit: int,
) -> int:
    printed = 0
    for repo in repositories[: max(0, limit)]:
        reasons = rejection_reasons(repo, filters)
        status = "matched" if not reasons else ",".join(reasons)
        print(
            "filter "
            f"{repo.get('full_name')} "
            f"language={repo.get('language')} "
            f"stars={repo.get('stargazers_count')} "
            f"created_at={repo.get('created_at')} "
            f"updated_at={repo.get('updated_at')} "
            f"reasons={status}",
            file=sys.stderr,
        )
        printed += 1
    return printed


def _print_rejection_summary(counts: Counter[str]) -> None:
    if counts:
        summary = ", ".join(f"{reason}={count}" for reason, count in counts.most_common())
        print(f"no records matched local filters; rejection_summary: {summary}", file=sys.stderr)


def _print_planned_queries(planned_queries: list[Any]) -> None:
    if len(planned_queries) == 1:
        print(planned_queries[0].query)
        return
    for index, planned in enumerate(planned_queries, start=1):
        print(f"{index}/{len(planned_queries)} {planned.query}")


def _global_max_results(value: Any) -> int | None:
    if value in (None, "", "all"):
        return None
    return max(1, int(value))
