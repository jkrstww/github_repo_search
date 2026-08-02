from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .filters import parse_datetime
from .jsonl import load_jsonl, overwrite_jsonl


Predicate = Callable[[dict[str, Any]], bool]


@dataclass(frozen=True)
class FilterStep:
    name: str
    suffix: str
    predicate: Predicate


@dataclass(frozen=True)
class FilterSummary:
    step: FilterStep
    input_path: Path
    output_path: Path
    input_count: int
    output_count: int


def default_filter_steps() -> list[FilterStep]:
    return [
        stars_gt10_step(),
        FilterStep(
            name="updated at or after 2026-01-01",
            suffix="updated_after2026",
            predicate=lambda record: _updated_at_or_after(record, "2026-01-01"),
        ),
    ]


def harmony_filter_steps() -> list[FilterStep]:
    return [
        stars_gt10_step(),
        FilterStep(
            name="language == TypeScript",
            suffix="language_typescript",
            predicate=lambda record: _language_is(record, "TypeScript"),
        ),
    ]


def filter_steps_for_pipeline(name: str) -> list[FilterStep]:
    pipelines = {
        "default": default_filter_steps,
        "harmony": harmony_filter_steps,
    }
    try:
        return pipelines[name]()
    except KeyError as exc:
        raise ValueError(f"unknown filter pipeline: {name}") from exc


def stars_gt10_step() -> FilterStep:
    return FilterStep(
        name="stars > 10",
        suffix="stars_gt10",
        predicate=lambda record: _count(record, "stargazers_count") > 10,
    )


def run_filter_pipeline(
    input_path: str | Path,
    *,
    steps: list[FilterStep] | None = None,
) -> list[FilterSummary]:
    current_path = Path(input_path)
    summaries: list[FilterSummary] = []

    for step in steps or default_filter_steps():
        input_records = load_jsonl(current_path)
        output_records = [record for record in input_records if step.predicate(record)]
        output_path = suffixed_jsonl_path(current_path, step.suffix)
        overwrite_jsonl(output_path, output_records)
        summaries.append(
            FilterSummary(
                step=step,
                input_path=current_path,
                output_path=output_path,
                input_count=len(input_records),
                output_count=len(output_records),
            )
        )
        current_path = output_path

    return summaries


def suffixed_jsonl_path(input_path: str | Path, suffix: str) -> Path:
    path = Path(input_path)
    return path.with_name(f"{path.stem}_{suffix}{path.suffix}")


def _count(record: dict[str, Any], key: str) -> int:
    value = record.get(key)
    if value in (None, ""):
        return 0
    return int(value)


def _updated_at_or_after(record: dict[str, Any], date_text: str) -> bool:
    updated_at = parse_datetime(record.get("updated_at"))
    threshold = datetime.fromisoformat(date_text).replace(tzinfo=timezone.utc)
    return updated_at is not None and updated_at >= threshold


def _language_is(record: dict[str, Any], expected: str) -> bool:
    return str(record.get("language") or "") == expected
