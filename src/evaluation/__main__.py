from __future__ import annotations

import argparse
import json

from .track_evaluator import evaluate_track_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate an agent response track")
    parser.add_argument("track", help="Path to trajectory.json")
    parser.add_argument("--output", help="Optional evaluation JSON output path")
    args = parser.parse_args()
    result = evaluate_track_file(args.track, output_path=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
