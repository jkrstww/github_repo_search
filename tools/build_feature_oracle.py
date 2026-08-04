from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from arkts_syntax_tree.oracle_generator import generate_feature_oracle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="为 ArkTS feature_implement instance 生成 oracle 测试")
    parser.add_argument("instance", help="feature_implement instance 目录")
    parser.add_argument("repo", help="对应的原始仓库根目录")
    parser.add_argument(
        "--output",
        "--output-dir",
        dest="output_dir",
        type=Path,
        default=None,
        help="oracle 输出目录；默认写入 instance/oracle",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    artifact = generate_feature_oracle(args.instance, args.repo, output_dir=args.output_dir)
    print(
        json.dumps(
            {"plan_path": str(artifact.plan_path), "test_path": str(artifact.test_path)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
