"""Lightweight syntax tree extraction for HarmonyOS ArkTS/ETS projects."""

from .parser import (
    ParsedFile,
    SyntaxNode,
    build_repository_summary,
    iter_source_files,
    parse_repository,
    parse_source,
    write_syntax_tree_outputs,
)
from .migration import detect_android_calls
from .repository_scan import scan_repository_android_calls
from .bug_instance import CandidateAnalysis, create_bug_instance, find_bug_candidates

__all__ = [
    "CandidateAnalysis",
    "ParsedFile",
    "SyntaxNode",
    "build_repository_summary",
    "create_bug_instance",
    "detect_android_calls",
    "find_bug_candidates",
    "iter_source_files",
    "parse_repository",
    "parse_source",
    "scan_repository_android_calls",
    "write_syntax_tree_outputs",
]
