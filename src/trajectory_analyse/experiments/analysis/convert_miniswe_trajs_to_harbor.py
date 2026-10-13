#!/usr/bin/env python3
"""Convert mini-swe-agent trajectories to Harbor's ATIF format.

The input directory is expected to contain mini-swe-agent ``*.traj.json`` (or
``*.traj``) files, normally one file below a directory named after each
instance.  The output mirrors that directory layout and stores each converted
file as ``trajectory.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any, Callable


Converter = Callable[[dict[str, Any], str], Any]


def _harbor_python() -> str | None:
    """Return the Python interpreter used by the installed Harbor CLI."""
    harbor = shutil.which("harbor")
    if not harbor:
        return None
    try:
        first_line = Path(harbor).read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return None
    if first_line.startswith("#!"):
        interpreter = shlex.split(first_line[2:])[0]
        if Path(interpreter).exists() and interpreter != sys.executable:
            return interpreter
    return None


def _load_converter() -> Converter:
    """Load Harbor's own mini-swe-agent adapter lazily.

    Keeping this import lazy lets callers inspect or test the file without
    requiring Harbor until an actual conversion is requested.
    """
    try:
        from harbor.agents.installed.mini_swe_agent import (
            convert_mini_swe_agent_to_atif,
        )
    except ModuleNotFoundError as exc:
        if exc.name == "harbor":
            raise RuntimeError(
                "Harbor is not importable. Run this script with Harbor's Python "
                "interpreter (for example: `harbor-python script.py ...`), or "
                "install Harbor in the current environment."
            ) from exc
        raise
    return convert_mini_swe_agent_to_atif


def _strip_trajectory_suffix(name: str) -> str:
    for suffix in (".traj.json", ".traj", ".json"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


def _instance_id(data: dict[str, Any], source: Path, input_dir: Path) -> str:
    value = data.get("instance_id")
    if isinstance(value, str) and value:
        return value
    if source.parent != input_dir:
        return source.parent.name
    return _strip_trajectory_suffix(source.name)


def find_trajectory_files(input_dir: Path) -> list[Path]:
    """Find supported mini-swe-agent trajectory files in deterministic order."""
    files = {
        path
        for pattern in ("*.traj.json", "*.traj")
        for path in input_dir.rglob(pattern)
        if path.is_file()
    }
    return sorted(files)


def _destination_for(
    source: Path, input_dir: Path, output_dir: Path, instance_id: str
) -> Path:
    relative_parent = source.relative_to(input_dir).parent
    # Also support a flat input directory without allowing every file to
    # overwrite the same output path.
    if relative_parent == Path("."):
        relative_parent = Path(instance_id)
    return output_dir / relative_parent / "trajectory.json"


def convert_directory(
    input_dir: Path,
    output_dir: Path | None = None,
    *,
    continue_on_error: bool = False,
) -> tuple[int, int]:
    """Convert all trajectories and return ``(converted, failed)`` counts."""
    input_dir = input_dir.expanduser().resolve()
    if not input_dir.is_dir():
        raise ValueError(f"Input trajectory directory does not exist: {input_dir}")

    output_dir = (
        output_dir.expanduser().resolve()
        if output_dir is not None
        else input_dir.parent / f"{input_dir.name}_harbor"
    )
    if output_dir == input_dir:
        raise ValueError("Output directory must differ from the input directory")

    files = find_trajectory_files(input_dir)
    if not files:
        raise ValueError(f"No .traj.json or .traj files found under {input_dir}")

    converter = _load_converter()
    destinations: dict[Path, Path] = {}
    converted = failed = 0

    for source in files:
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("trajectory JSON must contain an object")
            instance_id = _instance_id(data, source, input_dir)
            destination = _destination_for(source, input_dir, output_dir, instance_id)
            previous = destinations.get(destination)
            if previous is not None and previous != source:
                raise ValueError(
                    f"multiple input files map to {destination}: {previous} and {source}"
                )
            destinations[destination] = source

            trajectory = converter(data, instance_id)
            payload = trajectory.to_json_dict()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            converted += 1
            print(f"converted {source} -> {destination}")
        except Exception as exc:  # report the file while allowing batch conversion
            failed += 1
            print(f"failed {source}: {exc}", file=sys.stderr)
            if not continue_on_error:
                raise

    return converted, failed


def _maybe_reexec_with_harbor_python() -> None:
    """Re-run the CLI under Harbor's interpreter when available.

    The Harbor executable installed by uv commonly has its own Python 3.x
    environment, while the repository's default interpreter does not include
    the ``harbor`` package.
    """
    if os.environ.get("MINISWE_HARBOR_RUNTIME"):
        return
    interpreter = _harbor_python()
    if interpreter is None:
        return
    env = os.environ.copy()
    env["MINISWE_HARBOR_RUNTIME"] = "1"
    os.execvpe(interpreter, [interpreter, *sys.argv], env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert mini-swe-agent trajectories to Harbor ATIF trajectories."
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        help="Directory containing mini-swe-agent trajectories",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory (default: sibling <input-name>_harbor directory)",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Convert remaining files after an individual file fails",
    )
    args = parser.parse_args(argv)

    try:
        _maybe_reexec_with_harbor_python()
        converted, failed = convert_directory(
            args.input_dir,
            args.output_dir,
            continue_on_error=args.continue_on_error,
        )
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    except Exception as exc:
        print(f"conversion failed: {exc}", file=sys.stderr)
        return 1

    print(f"converted {converted} trajectory(s); {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
