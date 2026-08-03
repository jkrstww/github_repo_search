from __future__ import annotations

import argparse
from pathlib import Path


TARGET = Path("entry/src/main/ets/pages/view/Reader/ReaderPage3.ets")
EXPECTED_LINES = (
    "{ fontColor: '#000000', themeColor: '#FFFFFF', themeBgImg: '' },",
    "{ fontColor: '#000000', themeColor: '#c6b6a4', themeBgImg: '' },",
    "{ fontColor: '#000000', themeColor: '#C5E7CE', themeBgImg: '' },",
    "{ fontColor: '#FFFFFF', themeColor: '#202224', themeBgImg: '' },",
)
REJECTED_LINES = (
    "{ fontColor: '#202224', themeColor: '#FFFFFF', themeBgImg: '' },",
    "{ fontColor: '#202224', themeColor: '#ffbf9263', themeBgImg: '' },",
    "{ fontColor: '#202224', themeColor: '#C5E7CE', themeBgImg: '' },",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the Legado Harmony PR #349 fix.")
    parser.add_argument("repo", nargs="?", type=Path, default=Path("test_project/legado-Harmony"))
    args = parser.parse_args()

    target = args.repo / TARGET
    if not target.is_file():
        raise SystemExit(f"target file not found: {target}")

    source = target.read_text(encoding="utf-8")
    missing = [line for line in EXPECTED_LINES if source.count(line) != 1]
    remaining = [line for line in REJECTED_LINES if line in source]
    if missing or remaining:
        if missing:
            print("missing or duplicated expected lines:")
            for line in missing:
                print(f"  {line}")
        if remaining:
            print("obsolete color lines still present:")
            for line in remaining:
                print(f"  {line}")
        return 1

    print("Legado Harmony PR #349 static verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
