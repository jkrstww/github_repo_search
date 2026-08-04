from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from arkts_syntax_tree.feature_instance import create_feature_instance, find_feature_candidates


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="构造 ArkTS 接口/基类实现补全 benchmark instance")
    parser.add_argument("repo", help="待分析的仓库根目录")
    parser.add_argument("--syntax-tree", help="可选的语法树 JSONL；省略时直接解析仓库")
    parser.add_argument(
        "--output",
        "--output-dir",
        dest="output_dir",
        type=Path,
        default=PROJECT_ROOT / "instances" / "feature_implement",
        help="instance 输出根目录；默认 ./instances/feature_implement",
    )
    parser.add_argument("--instance-id", help="自定义 instance id")
    parser.add_argument("--min-implementations", type=int, default=2)
    parser.add_argument("--target-name", help="限定候选抽象节点名称或路径")
    parser.add_argument(
        "--include-structural-usage",
        action="store_true",
        help="允许把仅导入并使用 interface 类型的文件作为候选；默认只接受 implements/extends",
    )
    parser.add_argument("--list", action="store_true", help="只输出候选节点，不创建 instance")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    repo = Path(args.repo)
    if args.list:
        candidates = find_feature_candidates(
            repo,
            args.syntax_tree,
            min_implementation_files=args.min_implementations,
            target_name=args.target_name,
            include_structural_usage=args.include_structural_usage,
        )
        print(json.dumps([candidate.to_dict() for candidate in candidates], ensure_ascii=False, indent=2))
        return 0

    metadata = create_feature_instance(
        repo,
        output_dir=args.output_dir,
        syntax_tree_path=args.syntax_tree,
        min_implementation_files=args.min_implementations,
        target_name=args.target_name,
        include_structural_usage=args.include_structural_usage,
        instance_id=args.instance_id,
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
