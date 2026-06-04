"""tree-sitter 기반 구조/데이터플로우 분석.

정규식 휴리스틱(pr_analysis.py)이 보지 못하는 '사실'을 AST에서 결정적으로 추출한다.
- 죽은 인자(dead parameter): 변경된 메서드의 파라미터가 변경 후 본문에서 더 이상
  참조되지 않는 경우. (예: 소유권 가드 삭제로 ownerId가 미사용이 된 케이스)
- 공유 가변 상태(shared mutable field): 인스턴스 컬렉션 필드가 변경 메서드 안에서
  add/remove 등으로 변형되는 경우. Spring 싱글톤(@Service 등)이면 동시성 우려.

판단(위험도)은 내지 않는다. 다운스트림 LLM이 소비할 '근거 있는 관찰'만 만든다.
tree-sitter나 문법이 없으면 조용히 빈 결과를 돌려주어 파이프라인을 막지 않는다.
"""

from __future__ import annotations

import re
from typing import Any

SUPPORTED_SUFFIXES = (".java",)

COLLECTION_TYPES = {
    "List", "ArrayList", "LinkedList", "Collection", "Set", "HashSet",
    "TreeSet", "LinkedHashSet", "Map", "HashMap", "TreeMap", "LinkedHashMap",
    "ConcurrentHashMap", "Queue", "Deque", "ArrayDeque", "Stack", "Vector",
}
MUTATION_METHODS = {
    "add", "addAll", "remove", "removeAll", "removeIf", "clear", "set",
    "put", "putAll", "putIfAbsent", "replace", "merge", "compute",
    "push", "pop", "poll", "offer", "sort",
}
# Spring 등에서 기본 싱글톤으로 관리되는 스테레오타입 → 인스턴스 가변 상태가 공유됨.
SINGLETON_ANNOTATIONS = {
    "Service", "Component", "Repository", "Controller", "RestController",
    "Configuration", "Bean",
}
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_PARSER = None
_PARSER_READY = False


def _get_parser():
    """Java 파서를 지연 로딩한다. 의존성/문법이 없으면 None."""
    global _PARSER, _PARSER_READY
    if _PARSER_READY:
        return _PARSER

    _PARSER_READY = True
    try:
        import tree_sitter_java as tsj
        from tree_sitter import Language, Parser

        _PARSER = Parser(Language(tsj.language()))
    except Exception:
        _PARSER = None
    return _PARSER


def analyze_changed_file_structure(path: str, patch: str, content: str | None) -> dict[str, Any]:
    """변경 파일 하나에서 구조적 관찰(죽은 인자/공유 가변 상태)을 추출한다."""
    empty = {"dead_parameters": [], "shared_mutable_fields": [], "available": False}
    if not path.lower().endswith(SUPPORTED_SUFFIXES):
        return empty

    parser = _get_parser()
    if parser is None:
        return empty

    added_lines, removed_identifiers = _parse_patch(patch or "")

    # content(head 전체 본문)가 있으면 고신뢰. 없으면 patch에서 변경 후 코드를 재구성하되,
    # 부분 본문일 수 있으므로 삭제 줄에 등장한 식별자로 교차검증된 경우만 방출한다.
    if content:
        source = content
        confident = True
    else:
        source = _reconstruct_after_text(patch or "")
        confident = False
        if not source.strip():
            return empty

    try:
        source_bytes = source.encode("utf-8")
        tree = parser.parse(source_bytes)
    except Exception:
        return empty

    dead_parameters: list[dict[str, Any]] = []
    shared_mutable_fields: list[dict[str, Any]] = []

    for class_node in _iter_nodes(tree.root_node, "class_declaration"):
        class_name = _field_text(class_node, "name", source_bytes)
        annotations = _class_annotations(class_node, source_bytes)
        collection_fields = _collection_fields(class_node, source_bytes)

        for method_node in _iter_nodes(class_node, "method_declaration"):
            if not _method_is_changed(method_node, added_lines):
                continue

            body_node = method_node.child_by_field_name("body")
            if body_node is None:
                continue
            body_identifiers = _identifiers_in(body_node, source_bytes)
            method_name = _field_text(method_node, "name", source_bytes)

            # --- 죽은 인자 ---
            for param_name in _parameter_names(method_node, source_bytes):
                if param_name in body_identifiers:
                    continue
                corroborated = param_name in removed_identifiers
                if not confident and not corroborated:
                    continue
                dead_parameters.append({
                    "method": method_name,
                    "parameter": param_name,
                    "confidence": "high" if (confident and corroborated) else "medium",
                    "evidence": (
                        f"'{method_name}'의 파라미터 '{param_name}'가 변경 후 메서드 본문에서 "
                        f"참조되지 않습니다"
                        + ("(삭제된 코드에만 등장)." if corroborated else ".")
                    ),
                })

            # --- 공유 가변 상태 ---
            for field in collection_fields:
                ops = _mutation_ops_on(body_node, field["name"], source_bytes)
                if not ops:
                    continue
                singleton = sorted(annotations & SINGLETON_ANNOTATIONS)
                shared_mutable_fields.append({
                    "field": field["name"],
                    "type": field["type"],
                    "method": method_name,
                    "operations": ops,
                    "is_final": field["is_final"],
                    "class_annotations": sorted(annotations),
                    "evidence": (
                        f"{class_name}의 인스턴스 컬렉션 필드 '{field['name']}'({field['type']})가 "
                        f"'{method_name}'에서 {', '.join(ops)}로 변형됩니다"
                        + (
                            f". {class_name}는 {', '.join('@' + a for a in singleton)}(기본 싱글톤)이라 "
                            "동시 요청 간 공유 가변 상태입니다."
                            if singleton else
                            ". 인스턴스가 공유되면 동시성/정합성 검토가 필요합니다."
                        )
                    ),
                })

    return {
        "dead_parameters": dead_parameters,
        "shared_mutable_fields": shared_mutable_fields,
        "available": True,
    }


# --------------------------------------------------------------------------- #
# patch 파싱
# --------------------------------------------------------------------------- #
def _parse_patch(patch: str) -> tuple[set[int], set[str]]:
    """추가된 줄의 (변경 후) 라인 번호 집합과, 삭제된 줄에 등장한 식별자 집합을 반환."""
    added_lines: set[int] = set()
    removed_identifiers: set[str] = set()
    new_line = 0

    for line in patch.splitlines():
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            new_line = int(match.group(1)) if match else new_line
            continue
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            added_lines.add(new_line)
            new_line += 1
        elif line.startswith("-"):
            removed_identifiers.update(_IDENTIFIER_RE.findall(line[1:]))
        else:
            new_line += 1

    return added_lines, removed_identifiers


def _reconstruct_after_text(patch: str) -> str:
    """content가 없을 때 patch의 context+added 줄로 변경 후 코드를 근사 복원."""
    lines = []
    for line in patch.splitlines():
        if line.startswith("@@") or line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            lines.append(line[1:])
        elif line.startswith("-"):
            continue
        elif line.startswith(" "):
            lines.append(line[1:])
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# tree-sitter 헬퍼
# --------------------------------------------------------------------------- #
def _iter_nodes(node, type_name: str):
    """주어진 타입의 노드를 재귀적으로 순회한다."""
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == type_name:
            yield current
        stack.extend(current.children)


def _txt(node, source_bytes: bytes) -> str:
    if node is None:
        return ""
    return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _field_text(node, field: str, source_bytes: bytes) -> str:
    return _txt(node.child_by_field_name(field), source_bytes)


def _method_is_changed(method_node, added_lines: set[int]) -> bool:
    """메서드의 라인 범위가 patch의 추가 라인과 겹치면 '변경된 메서드'로 본다."""
    if not added_lines:
        return False
    start = method_node.start_point[0] + 1
    end = method_node.end_point[0] + 1
    return any(start <= line <= end for line in added_lines)


def _parameter_names(method_node, source_bytes: bytes) -> list[str]:
    params_node = method_node.child_by_field_name("parameters")
    if params_node is None:
        return []
    names = []
    for child in params_node.children:
        if child.type in ("formal_parameter", "spread_parameter"):
            name = child.child_by_field_name("name")
            if name is not None:
                names.append(_txt(name, source_bytes))
    return names


def _identifiers_in(node, source_bytes: bytes) -> set[str]:
    return {_txt(n, source_bytes) for n in _iter_nodes(node, "identifier")}


def _class_annotations(class_node, source_bytes: bytes) -> set[str]:
    annotations: set[str] = set()
    for child in class_node.children:
        if child.type != "modifiers":
            continue
        for ann in child.children:
            if ann.type in ("marker_annotation", "annotation"):
                name = ann.child_by_field_name("name")
                annotations.add(_txt(name, source_bytes).lstrip("@"))
    return annotations


def _collection_fields(class_node, source_bytes: bytes) -> list[dict[str, Any]]:
    body = class_node.child_by_field_name("body")
    if body is None:
        return []
    fields = []
    for field_node in body.children:
        if field_node.type != "field_declaration":
            continue
        type_node = field_node.child_by_field_name("type")
        base_type = _base_type_name(type_node, source_bytes)
        if base_type not in COLLECTION_TYPES:
            continue
        is_final = any(
            _txt(child, source_bytes) == "final"
            for modifiers in field_node.children if modifiers.type == "modifiers"
            for child in modifiers.children
        )
        for declarator in field_node.children:
            if declarator.type != "variable_declarator":
                continue
            name = declarator.child_by_field_name("name")
            if name is not None:
                fields.append({
                    "name": _txt(name, source_bytes),
                    "type": _txt(type_node, source_bytes),
                    "is_final": is_final,
                })
    return fields


def _base_type_name(type_node, source_bytes: bytes) -> str:
    if type_node is None:
        return ""
    if type_node.type == "generic_type":
        for child in type_node.children:
            if child.type in ("type_identifier", "scoped_type_identifier"):
                return _txt(child, source_bytes).split(".")[-1]
    return _txt(type_node, source_bytes).split("<")[0].split(".")[-1].strip()


def _mutation_ops_on(body_node, field_name: str, source_bytes: bytes) -> list[str]:
    """body 안에서 `field.add(...)`/`this.field.remove(...)` 같은 변형 호출을 수집한다."""
    ops = []
    seen = set()
    for inv in _iter_nodes(body_node, "method_invocation"):
        name_node = inv.child_by_field_name("name")
        object_node = inv.child_by_field_name("object")
        if name_node is None or object_node is None:
            continue
        op = _txt(name_node, source_bytes)
        if op not in MUTATION_METHODS:
            continue
        if _object_refers_to(object_node, field_name, source_bytes) and op not in seen:
            seen.add(op)
            ops.append(op)
    return ops


def _object_refers_to(object_node, field_name: str, source_bytes: bytes) -> bool:
    text = _txt(object_node, source_bytes)
    return text == field_name or text == f"this.{field_name}"
