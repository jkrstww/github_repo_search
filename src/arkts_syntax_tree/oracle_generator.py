from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InterfaceField:
    name: str
    type_text: str
    optional: bool


@dataclass(frozen=True)
class TypeAliasInfo:
    name: str
    type_text: str
    sample_value: str | None


@dataclass(frozen=True)
class ClassInfo:
    name: str
    extends_text: str | None
    default_export: bool
    methods: tuple[str, ...]


@dataclass(frozen=True)
class OracleArtifact:
    plan_path: Path
    test_path: Path


def generate_feature_oracle(
    instance_dir: str | Path,
    repo_root: str | Path,
    *,
    output_dir: str | Path | None = None,
) -> OracleArtifact:
    instance = Path(instance_dir).resolve()
    repo = Path(repo_root).resolve()
    metadata = json.loads((instance / "instance.json").read_text(encoding="utf-8"))
    output = Path(output_dir).resolve() if output_dir is not None else instance / "oracle"
    output.mkdir(parents=True, exist_ok=True)

    interface_name = metadata["target"]["abstract_node"]["name"]
    source_candidates = [
        metadata["target"]["abstract_node"]["path"],
        metadata.get("mask", {}).get("path"),
    ]
    target_path = ""
    fields: list[InterfaceField] = []
    enums: dict[str, list[str]] = {}
    type_aliases: list[TypeAliasInfo] = []
    const_objects: dict[str, dict[str, str]] = {}
    for candidate_path in source_candidates:
        if not candidate_path:
            continue
        candidate_source = repo / candidate_path
        if not candidate_source.is_file():
            continue
        candidate_text = candidate_source.read_text(encoding="utf-8", errors="replace")
        candidate_fields = _extract_interface_fields(candidate_text, interface_name)
        candidate_enums = _extract_enums(candidate_text)
        candidate_type_aliases = _extract_type_aliases(candidate_text)
        candidate_const_objects = _extract_const_objects(candidate_text)
        if candidate_fields or candidate_enums or candidate_type_aliases or candidate_const_objects:
            target_path = candidate_path
            fields = candidate_fields
            enums = candidate_enums
            type_aliases = candidate_type_aliases
            const_objects = candidate_const_objects
            break
    if not (fields or enums or type_aliases or const_objects):
        return _generate_class_oracle(metadata, repo, output)

    plan = {
        "schema_version": 1,
        "instance_id": metadata["instance_id"],
        "target_path": target_path,
        "source_path": target_path,
        "interface_name": interface_name,
        "fields": [
            {
                **field.__dict__,
                "sample_value": _sample_value(field.type_text, field.optional, enums),
            }
            for field in fields
        ],
        "enums": enums,
        "type_aliases": [alias.__dict__ for alias in type_aliases],
        "const_objects": const_objects,
        "cases": _build_cases(interface_name, fields, enums, type_aliases, const_objects),
    }
    plan_path = output / "test_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    test_path = output / "Oracle.test.ets"
    test_path.write_text(
        _render_test_suite(
            metadata,
            target_path,
            interface_name,
            fields,
            enums,
            type_aliases,
            const_objects,
        ),
        encoding="utf-8",
    )
    return OracleArtifact(plan_path=plan_path, test_path=test_path)


def _generate_class_oracle(
    metadata: dict[str, Any],
    repo: Path,
    output: Path,
) -> OracleArtifact:
    mask_path = metadata["mask"]["path"]
    abstract_path = metadata["target"]["abstract_node"]["path"]
    mask_source = repo / mask_path
    abstract_source = repo / abstract_path
    if not mask_source.is_file():
        raise FileNotFoundError(f"masked source does not exist: {mask_source}")
    if not abstract_source.is_file():
        raise FileNotFoundError(f"abstract source does not exist: {abstract_source}")

    mask_text = mask_source.read_text(encoding="utf-8", errors="replace")
    abstract_text = abstract_source.read_text(encoding="utf-8", errors="replace")
    class_name = metadata["mask"]["declarations"][0]
    base_name = metadata["target"]["abstract_node"]["name"]
    class_info = _extract_class_info(mask_text, class_name)
    base_info = _extract_class_info(abstract_text, base_name)
    if class_info is None:
        raise ValueError(f"no class oracle can be generated from {mask_path}")
    if class_info.extends_text is None or base_name not in class_info.extends_text:
        raise ValueError(f"{class_name} does not extend {base_name}")

    plan = {
        "schema_version": 1,
        "instance_id": metadata["instance_id"],
        "target_path": mask_path,
        "source_path": mask_path,
        "abstract_path": abstract_path,
        "class_name": class_info.name,
        "base_class_name": base_name,
        "extends_text": class_info.extends_text,
        "methods": list(class_info.methods),
        "cases": _build_class_cases(class_info, base_name),
    }
    plan_path = output / "test_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    test_path = output / "Oracle.test.ets"
    test_path.write_text(
        _render_class_test_suite(metadata, mask_path, abstract_path, class_info, base_info),
        encoding="utf-8",
    )
    return OracleArtifact(plan_path=plan_path, test_path=test_path)


def _extract_interface_fields(source_text: str, interface_name: str) -> list[InterfaceField]:
    pattern = re.compile(rf"\b(?:export\s+)?(?:default\s+)?interface\s+{re.escape(interface_name)}\s*{{")
    match = pattern.search(source_text)
    if not match:
        return []
    start = source_text.find("{", match.start())
    end = _find_matching_brace(source_text, start)
    if start < 0 or end < 0:
        return []
    body = source_text[start + 1 : end]
    fields: list[InterfaceField] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        if line.endswith(";"):
            line = line[:-1].strip()
        field_match = re.match(r"(?P<name>[A-Za-z_$][\w$]*)(?P<optional>\?)?\s*:\s*(?P<type>.+)$", line)
        if field_match:
            fields.append(
                InterfaceField(
                    name=field_match.group("name"),
                    type_text=field_match.group("type").strip(),
                    optional=field_match.group("optional") == "?",
                )
            )
    return fields


def _extract_enums(source_text: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for match in re.finditer(r"\bexport\s+enum\s+(?P<name>[A-Za-z_$][\w$]*)\s*{(?P<body>[^}]*)}", source_text, re.S):
        members: list[str] = []
        for part in match.group("body").split(","):
            name = part.strip().split("=", 1)[0].strip()
            if name:
                members.append(name)
        result[match.group("name")] = members
    return result


def _extract_type_aliases(source_text: str) -> list[TypeAliasInfo]:
    result: list[TypeAliasInfo] = []
    for match in re.finditer(
        r"\bexport\s+type\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?P<body>[^;\n]+)",
        source_text,
    ):
        type_text = match.group("body").strip()
        result.append(
            TypeAliasInfo(
                name=match.group("name"),
                type_text=type_text,
                sample_value=_sample_value(type_text, False, {}),
            )
        )
    return result


def _extract_const_objects(source_text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    pattern = re.compile(
        r"\bexport\s+const\s+(?P<name>[A-Za-z_$][\w$]*)\s*:\s*Record<[^>]+>\s*=\s*{(?P<body>[^}]*)}",
        re.S,
    )
    for match in pattern.finditer(source_text):
        entries: dict[str, str] = {}
        for raw_line in match.group("body").splitlines():
            line = raw_line.strip().rstrip(",")
            if not line:
                continue
            entry_match = re.match(r"(?P<key>'[^']+'|\"[^\"]+\"|[A-Za-z_$][\w$]*)\s*:\s*(?P<value>.+)", line)
            if entry_match:
                entries[entry_match.group("key").strip("'\"")] = entry_match.group("value").strip()
        result[match.group("name")] = entries
    return result


def _extract_class_info(source_text: str, class_name: str) -> ClassInfo | None:
    pattern = re.compile(
        rf"\b(?P<export>export\s+)?(?P<default>default\s+)?class\s+{re.escape(class_name)}(?:<[^{{\n]+>)?"
        rf"(?:\s+extends\s+(?P<extends>[^\{{\n]+?))?"
        rf"(?:\s+implements\s+[^\{{\n]+)?\s*{{",
        re.S,
    )
    match = pattern.search(source_text)
    if not match:
        return None
    start = source_text.find("{", match.start())
    end = _find_matching_brace(source_text, start)
    if start < 0 or end < 0:
        return None
    body = source_text[start + 1 : end]
    methods: list[str] = []
    blocked_names = {"for", "if", "switch", "while", "catch", "return", "try", "else", "do"}
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//") or line.startswith("/*") or line.startswith("*"):
            continue
        if line.startswith("private ") or line.startswith("protected "):
            continue
        if line.startswith("public "):
            line = line[len("public ") :].lstrip()
        method_match = re.match(r"(?P<name>[A-Za-z_$][\w$]*)\s*\(", line)
        if method_match and method_match.group("name") not in blocked_names:
            methods.append(method_match.group("name"))
    unique_methods: list[str] = []
    for method_name in methods:
        if method_name not in unique_methods:
            unique_methods.append(method_name)
    return ClassInfo(
        name=class_name,
        extends_text=(match.group("extends") or "").strip() or None,
        default_export=bool(match.group("default")),
        methods=tuple(unique_methods),
    )


def _build_cases(
    interface_name: str,
    fields: list[InterfaceField],
    enums: dict[str, list[str]],
    type_aliases: list[TypeAliasInfo],
    const_objects: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    if fields:
        cases.append(
            {
                "name": f"sample {interface_name} object",
                "kind": "interface_sample",
                "assertions": [field.name for field in fields],
            }
        )
    for enum_name, members in enums.items():
        cases.append(
            {
                "name": f"stable enum {enum_name}",
                "kind": "enum_stability",
                "members": members,
            }
        )
    for alias in type_aliases:
        cases.append(
            {
                "name": f"stable type alias {alias.name}",
                "kind": "type_alias_stability",
                "type_text": alias.type_text,
                "sample_value": alias.sample_value,
            }
        )
    for const_name, entries in const_objects.items():
        cases.append(
            {
                "name": f"stable const {const_name}",
                "kind": "const_mapping",
                "entries": entries,
            }
        )
    return cases


def _build_class_cases(class_info: ClassInfo, base_name: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = [
        {
            "name": f"keeps inheritance from {base_name}",
            "kind": "class_inheritance",
            "extends_text": class_info.extends_text,
        }
    ]
    if class_info.methods:
        cases.append(
            {
                "name": f"preserves method surface for {class_info.name}",
                "kind": "class_methods",
                "methods": list(class_info.methods),
            }
        )
    return cases


def _render_test_suite(
    metadata: dict[str, Any],
    target_path: str,
    interface_name: str,
    fields: list[InterfaceField],
    enums: dict[str, list[str]],
    type_aliases: list[TypeAliasInfo],
    const_objects: dict[str, dict[str, str]],
) -> str:
    module_import = _relative_import_for_target(target_path)
    imports = [f"import {{ describe, expect, it }} from '@ohos/hypium';", f"import {{ {interface_name}"]
    extra_names: list[str] = []
    for enum_name in enums:
        extra_names.append(enum_name)
    for alias in type_aliases:
        extra_names.append(alias.name)
    for const_name in const_objects:
        extra_names.append(const_name)
    if extra_names:
        imports[1] += ", " + ", ".join(extra_names)
    imports[1] += f" }} from '{module_import}';"

    lines = list(imports)
    lines += [
        "",
        f"export default function oracleFor{metadata['instance_id'].replace('-', '_')}() {{",
        f"  describe('{interface_name} oracle', function () {{",
    ]
    if fields:
        lines.extend(_render_interface_case(interface_name, fields, enums))
    for enum_name, members in enums.items():
        lines.extend(_render_enum_case(enum_name, members))
    for alias in type_aliases:
        lines.extend(_render_type_alias_case(alias))
    for const_name, entries in const_objects.items():
        lines.extend(_render_const_case(const_name, entries))
    lines.extend([
        "  })",
        "}",
        "",
    ])
    return "\n".join(lines)


def _render_class_test_suite(
    metadata: dict[str, Any],
    class_path: str,
    abstract_path: str,
    class_info: ClassInfo,
    base_info: ClassInfo | None,
) -> str:
    class_import = _relative_import_for_target(class_path)
    base_import = _relative_import_for_target(abstract_path)
    class_import_line = (
        f"import {class_info.name} from '{class_import}';"
        if class_info.default_export
        else f"import {{ {class_info.name} }} from '{class_import}';"
    )
    base_export_name = base_info.name if base_info and base_info.default_export else metadata["target"]["abstract_node"]["name"]
    base_import_line = (
        f"import {base_export_name} from '{base_import}';"
        if base_info and base_info.default_export
        else f"import {{ {base_export_name} }} from '{base_import}';"
    )
    lines = [
        "import { describe, expect, it } from '@ohos/hypium';",
        class_import_line,
        base_import_line,
        "",
        f"export default function oracleFor{metadata['instance_id'].replace('-', '_')}() {{",
        f"  describe('{class_info.name} oracle', function () {{",
        "    it('preserves inheritance relation', 0, function () {",
        f"      const instance = new {class_info.name}();",
        f"      expect(instance instanceof {base_export_name}).assertTrue();",
        "    })",
        "    it('keeps basic data source API', 0, function () {",
        f"      const instance = new {class_info.name}();",
        "      expect(typeof instance.totalCount).assertEqual('function');",
        "      expect(typeof instance.getData).assertEqual('function');",
        "      expect(typeof instance.getListData).assertEqual('function');",
        "      expect(typeof instance.addData).assertEqual('function');",
        "      expect(typeof instance.pushData).assertEqual('function');",
        "      expect(typeof instance.pushAllData).assertEqual('function');",
        "      expect(typeof instance.reloadNewData).assertEqual('function');",
        "    })",
        "    it('starts empty', 0, function () {",
        f"      const instance = new {class_info.name}();",
        "      expect(instance.totalCount()).assertEqual(0);",
        "      expect(instance.getListData().length).assertEqual(0);",
        "    })",
        "  })",
        "}",
        "",
    ]
    return "\n".join(lines)


def _render_interface_case(
    interface_name: str,
    fields: list[InterfaceField],
    enums: dict[str, list[str]],
) -> list[str]:
    object_lines = ["    it('records interface shape', 0, function () {", f"      const sample: {interface_name} = {{"]
    for field in fields:
        value = _sample_value(field.type_text, field.optional, enums)
        if value is None and not field.optional:
            raise ValueError(f"cannot synthesize sample value for required field {field.name}")
        if value is None:
            continue
        object_lines.append(f"        {field.name}: {value},")
    object_lines.append("      }")
    for field in fields:
        value = _sample_value(field.type_text, field.optional, enums)
        if value is None:
            continue
        object_lines.append(f"      expect(sample.{field.name}).assertEqual({value})")
    object_lines.append("    })")
    return object_lines


def _render_type_alias_case(alias: TypeAliasInfo) -> list[str]:
    if alias.sample_value is None:
        raise ValueError(f"cannot synthesize sample value for type alias {alias.name}")
    return [
        f"    it('keeps {alias.name} stable', 0, function () {{",
        f"      const sample: {alias.name} = {alias.sample_value}",
        f"      expect(sample).assertEqual({alias.sample_value})",
        "    })",
    ]


def _render_enum_case(enum_name: str, members: list[str]) -> list[str]:
    lines = [f"    it('keeps {enum_name} stable', 0, function () {{"]
    for index, member in enumerate(members):
        lines.append(f"      expect({enum_name}.{member}).assertEqual({index})")
    lines.append("    })")
    return lines


def _render_const_case(const_name: str, entries: dict[str, str]) -> list[str]:
    lines = [f"    it('keeps {const_name} stable', 0, function () {{"]
    for key, value in entries.items():
        lines.append(f"      expect({const_name}.{key}).assertEqual({value})")
    lines.append("    })")
    return lines


def _sample_value(type_text: str, optional: bool, enums: dict[str, list[str]]) -> str | None:
    normalized = type_text.replace(" ", "")
    literal = _literal_value(normalized)
    if literal is not None:
        return literal
    if "|" in normalized:
        parts = [part.strip() for part in normalized.split("|")]
        for part in parts:
            literal = _literal_value(part)
            if literal is not None:
                return literal
    if "|" in normalized and "string" in normalized and "number" in normalized:
        return "'sample'"
    if normalized in {"string", "String"}:
        return "'sample'"
    if normalized in {"number", "Number"}:
        return "1"
    if normalized in {"boolean", "Boolean"}:
        return "true"
    if normalized.endswith("[]"):
        return "[]"
    for enum_name, members in enums.items():
        if normalized == enum_name and members:
            return f"{enum_name}.{members[0]}"
    if "Resource" in normalized:
        return "{} as Resource"
    if optional:
        return "undefined"
    return None


def _literal_value(type_text: str) -> str | None:
    if re.fullmatch(r"'[^']*'|\"[^\"]*\"", type_text):
        return type_text
    if re.fullmatch(r"-?\d+(?:\.\d+)?", type_text):
        return type_text
    if type_text in {"true", "false"}:
        return type_text
    return None


def _relative_import_for_target(target_path: str) -> str:
    target = Path(target_path)
    depth = len(target.parts) - 1
    prefix = "/".join([".."] * depth)
    return f"{prefix}/{target.with_suffix('').as_posix()}"


def _find_matching_brace(text: str, opening_index: int) -> int:
    depth = 0
    for index in range(opening_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1
