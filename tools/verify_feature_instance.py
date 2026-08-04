from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from arkts_syntax_tree.feature_instance import verify_feature_instance


def main() -> int:
    parser = argparse.ArgumentParser(description="验证接口/基类实现补全 benchmark instance")
    parser.add_argument("instance", help="instance 目录")
    parser.add_argument("repo", help="已应用 mask.patch、等待验证的仓库目录")
    args = parser.parse_args()
    result = verify_feature_instance(args.instance, args.repo)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
