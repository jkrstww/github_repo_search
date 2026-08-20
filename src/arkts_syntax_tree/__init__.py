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
from .bug_instance import (
    DEFAULT_MUTATION_OPERATORS,
    CandidateAnalysis,
    MutationSpec,
    create_bug_instance,
    enumerate_mutations,
    find_bug_candidates,
    select_candidate_mutation,
)
from .feature_instance import (
    FeatureCandidate,
    create_feature_instance,
    find_feature_candidates,
    verify_feature_instance,
)
from .oracle_generator import OracleArtifact, generate_feature_oracle

__all__ = [
    "CandidateAnalysis",
    "DEFAULT_MUTATION_OPERATORS",
    "FeatureCandidate",
    "MutationSpec",
    "OracleArtifact",
    "ParsedFile",
    "SyntaxNode",
    "build_repository_summary",
    "create_bug_instance",
    "create_feature_instance",
    "detect_android_calls",
    "enumerate_mutations",
    "find_bug_candidates",
    "find_feature_candidates",
    "iter_source_files",
    "generate_feature_oracle",
    "parse_repository",
    "parse_source",
    "scan_repository_android_calls",
    "select_candidate_mutation",
    "write_syntax_tree_outputs",
    "verify_feature_instance",
]
