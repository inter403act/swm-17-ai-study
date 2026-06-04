import json
import re
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Okapi

try:
    from agents.llm import invoke_solar
except ModuleNotFoundError:
    from commentory.ai.agents.llm import invoke_solar

try:
    from agents.code_structure import analyze_changed_file_structure
except ModuleNotFoundError:
    from commentory.ai.agents.code_structure import analyze_changed_file_structure

try:
    from state import PRState
except ModuleNotFoundError:
    from commentory.ai.state import PRState


TEST_KEYWORDS = ("test", "tests", "spec", "__tests__")
PATH_STOP_TERMS = {
    "app",
    "java",
    "main",
    "sample",
    "src",
    "test",
    "tests",
}
DOC_EXTENSIONS = {".md", ".mdx", ".rst", ".txt"}
CONFIG_FILES = {
    ".env",
    ".gitignore",
    "dockerfile",
    "docker-compose.yml",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
}
DOMAIN_KEYWORDS = {
    "authentication": ("auth", "jwt", "token", "login", "session", "permission"),
    "authorization": ("role", "acl", "permission", "authorize", "admin"),
    "payment": ("payment", "billing", "invoice", "checkout", "refund"),
    "database": ("migration", "schema", "repository", "entity", "model", "sql"),
    "api": ("controller", "route", "router", "endpoint", "api", "request", "response"),
    "ui": ("view", "page", "component", "button", "screen", "template"),
}
RISK_SIGNAL_KEYWORDS = {
    "auth": ("auth", "jwt", "token", "login", "session"),
    "security": ("secret", "password", "credential", "permission", "encrypt", "decrypt"),
    "database": ("migration", "schema", "transaction", "sql", "entity"),
    "public_api": ("@getmapping", "@postmapping", "@putmapping", "@deletemapping", "router", "endpoint"),
    "runtime_behavior": ("service", "controller", "handler", "middleware", "usecase"),
}
# 인가/소유권 검증(가드)이 diff에서 "삭제"되었는지 탐지하기 위한 어휘.
# 추가(+)가 아니라 삭제(-)된 줄에서만 검사하므로, 단순히 검증 메시지 문구만 바꾼
# 변경(가드는 그대로 유지)과 검증 자체를 제거한 변경을 구분할 수 있다.
ACCESS_CONTROL_TERMS = (
    "owner", "ownerid", "permission", "role", "authorize", "authorization",
    "acl", "forbidden", "unauthorized", "privilege", "admin", "grant",
)
GUARD_TOKENS = (
    "if", "throw", "require", "assert", "check", "validate",
    "equals", "==", "!=", "deny", "reject", "guard",
)
# 심볼 추출 시 걸러낼 언어 키워드 / 표준 타입성 토큰 (탐욕적 정규식 노이즈 방지).
SYMBOL_STOP_TOKENS = {"if", "for", "while", "switch", "catch", "return", "new", "else"}
# 검색어에서 제외할 영어 불용어 + 체인지타입 접두사 (일반명사 노이즈 방지).
ENGLISH_STOP_WORDS = {
    "and", "add", "the", "for", "with", "from", "into", "support", "update",
    "improve", "change", "fix", "feat", "chore", "refactor", "docs", "simplify",
}
SYMBOL_PATTERNS = (
    re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"\bdef\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
    re.compile(r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
    re.compile(r"\b(?:public|private|protected)?\s*(?:static\s+)?[A-Za-z0-9_<>,\[\]]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
)
TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|/[A-Za-z0-9_./{}:-]+")
CHUNK_SIZE = 40
CHUNK_OVERLAP = 10
MAX_CHANGED_FILES = 20
MAX_DIFF_CHARS = 60000
MAX_REPOSITORY_FILES = 200
MAX_CONTENT_CHUNKS = 400
MAX_LLM_EVIDENCE_ITEMS = 8
MAX_LLM_SNIPPET_CHARS = 1200
SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|github[_-]?token|token|secret|password|credential|private[_-]?key)"
        r"(\s*[:=]\s*)([\"']?)([^\s'\"&]+)([\"']?)"
    ),
    re.compile(r"\b(?:ghp|github_pat|sk|xoxb|xoxp|glpat)-[A-Za-z0-9_./+=-]{12,}\b"),
)
IMPORT_PATTERNS = (
    re.compile(r"^[+\- ]?\s*import\s+([A-Za-z0-9_.*]+);?", re.MULTILINE),
    re.compile(r"^[+\- ]?\s*from\s+([A-Za-z0-9_.]+)\s+import\s+[A-Za-z0-9_*,\s]+", re.MULTILINE),
    re.compile(r"^[+\- ]?\s*import\s+(?:type\s+)?[A-Za-z0-9_{}*,\s]+\s+from\s+[\"']([^\"']+)[\"']", re.MULTILINE),
    re.compile(r"^[+\- ]?\s*import\s+[\"']([^\"']+)[\"']", re.MULTILINE),
)
EVIDENCE_SYSTEM_PROMPT = """당신은 Commentory의 PR 분석 Agent 내부에서 동작하는 evidence 검증 보조 모듈입니다.

역할:
- 주어진 변경 파일 정보와 검색된 repository snippet 후보만 보고, 각 snippet이 PR 변경 근거로 왜 관련 있는지 판단합니다.
- 요약, 위험도 평가, 체크리스트, 승인/거절 의견은 생성하지 않습니다.
- snippet에 없는 사실을 추측하지 않습니다.
- 근거가 약하면 낮은 relevance와 짧은 이유를 남깁니다.

출력은 JSON 객체 하나만 반환합니다.

JSON 스키마:
{
  "items": [
    {
      "index": 0,
      "relevance": "high | medium | low",
      "evidence_reason": "이 snippet이 변경 근거로 관련 있는 이유",
      "supported_claims": ["snippet으로 확인 가능한 근거"],
      "uncertainty": "불확실한 지점"
    }
  ]
}
"""


def pr_analysis_agent(state: PRState) -> dict[str, Any]:
    """Build ImpactContext from collected PR data."""
    pr_data = state.get("pr_data") or {}

    return {
        "impact_context": build_impact_context(pr_data),
    }


def build_impact_context(pr_data: dict[str, Any]) -> dict[str, Any]:
    changed_files = pr_data.get("changed_files") or []
    limitation = _build_analysis_limitation(changed_files, _get_repository_file_contents(pr_data))
    scoped_changed_files = _limit_changed_files(changed_files)
    analyzed_files = [_analyze_changed_file(file_data) for file_data in scoped_changed_files]
    domains = _dedupe(item for file_data in analyzed_files for item in file_data["impact_domains"])
    risk_signals = _dedupe(item for file_data in analyzed_files for item in file_data["risk_signals"])
    imports = _extract_imports(scoped_changed_files)
    search_terms = _build_search_terms(analyzed_files, pr_data, imports)
    path_related_files = _find_related_files(analyzed_files, pr_data.get("repo_tree") or [], search_terms)
    import_related_files = _find_import_related_files(imports, pr_data.get("repo_tree") or [])
    retrieval_contents = _exclude_changed_files(
        _limit_repository_file_contents(_get_repository_file_contents(pr_data)),
        analyzed_files,
    )
    content_results = _search_related_contents(search_terms, retrieval_contents)
    direct_callers = _find_direct_callers(analyzed_files, retrieval_contents)
    evidence_candidates = _enrich_evidence_candidates_with_llm(
        analyzed_files,
        content_results + direct_callers,
    )
    related_files = _merge_related_files(path_related_files + import_related_files, evidence_candidates)
    related_tests = [item["file"] for item in related_files if _is_test_file(item["file"])]
    main_changes = _build_main_changes(analyzed_files)
    security_concerns = _build_security_concerns(analyzed_files)

    return {
        "change_intent": _infer_change_intent(pr_data, analyzed_files),
        "pr_change_type": _infer_pr_change_type(pr_data, analyzed_files),
        "main_changes": main_changes,
        "pr_summary": _build_summary(pr_data, analyzed_files, domains),
        "change_stats": _build_change_stats(changed_files),
        "changed_files": analyzed_files,
        "impact_scope": {
            "domains": domains,
            "affected_apis": _extract_affected_apis(changed_files),
            "affected_tests": related_tests,
            "related_files": [item["file"] for item in related_files],
        },
        "dependency_context": {
            "retrieval_strategy": "bm25_keyword_symbol_path",
            "search_terms": search_terms,
            "imports": imports,
            "related_files": related_files,
            "bm25_results": content_results,
            "direct_callers": direct_callers,
            "llm_evidence_used": any("llm_evidence" in item for item in evidence_candidates),
        },
        "test_coverage_signal": {
            "tests_added_or_modified": any(_is_test_file(file_data.get("filename", "")) for file_data in changed_files),
            "related_test_files": related_tests,
            "missing_test_concerns": _build_missing_test_concerns(risk_signals, related_tests),
        },
        "analysis_limited": limitation["limited"],
        "limitation_reason": limitation["reasons"],
        "comment_notice": _build_comment_notice(limitation),
        "risk_signals": risk_signals,
        "security_concerns": security_concerns,
        "structural_observations": _build_structural_observations(analyzed_files),
        "evidence": _build_evidence(analyzed_files, evidence_candidates),
    }


def _build_structural_observations(analyzed_files: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """AST(tree-sitter)로 추출한 데이터플로우 관찰을 위험 평가가 바로 볼 수 있게 모은다.

    판단(위험도)이 아니라 '근거 있는 사실'만 담는다: 죽은 인자, 공유 가변 상태.
    """
    dead_parameters = []
    shared_mutable_fields = []
    for file_data in analyzed_files:
        for item in file_data.get("dead_parameters", []):
            dead_parameters.append({"file": file_data["path"], **item})
        for item in file_data.get("shared_mutable_fields", []):
            shared_mutable_fields.append({"file": file_data["path"], **item})
    return {
        "dead_parameters": dead_parameters,
        "shared_mutable_fields": shared_mutable_fields,
    }


def _build_security_concerns(analyzed_files: list[dict[str, Any]]) -> list[str]:
    """삭제된 인가/소유권 가드를 위험 평가 단계가 바로 인지하도록 명시적 항목으로 정리한다."""
    concerns = []
    for file_data in analyzed_files:
        for line in file_data.get("removed_access_control", []):
            concerns.append(
                f"{file_data['path']}에서 인가/소유권 검증으로 보이는 코드가 삭제되었습니다: "
                f"{_mask_secrets(line)}"
            )
    return concerns


def _build_change_stats(changed_files: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "files_changed": len(changed_files),
        "lines_added": sum(int(file_data.get("additions") or 0) for file_data in changed_files),
        "lines_deleted": sum(int(file_data.get("deletions") or 0) for file_data in changed_files),
        "test_files_changed": sum(1 for file_data in changed_files if _is_test_file(file_data.get("filename", ""))),
    }


def _build_analysis_limitation(
    changed_files: list[dict[str, Any]],
    repository_file_contents: list[dict[str, Any]],
) -> dict[str, Any]:
    reasons = []
    diff_chars = sum(len(file_data.get("patch") or "") for file_data in changed_files)

    if len(changed_files) > MAX_CHANGED_FILES:
        reasons.append(f"변경 파일 수가 {MAX_CHANGED_FILES}개를 초과하여 일부 파일 중심으로 분석했습니다.")
    if diff_chars > MAX_DIFF_CHARS:
        reasons.append(f"diff 길이가 {MAX_DIFF_CHARS}자를 초과하여 일부 diff 중심으로 분석했습니다.")
    if len(repository_file_contents) > MAX_REPOSITORY_FILES:
        reasons.append(f"repository file content가 {MAX_REPOSITORY_FILES}개를 초과하여 일부 파일만 검색했습니다.")

    return {
        "limited": bool(reasons),
        "reasons": reasons,
    }


def _limit_changed_files(changed_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scoped_files = []
    consumed_diff_chars = 0

    for file_data in changed_files[:MAX_CHANGED_FILES]:
        copied_file = dict(file_data)
        patch = copied_file.get("patch") or ""
        remaining_diff_chars = MAX_DIFF_CHARS - consumed_diff_chars
        if remaining_diff_chars <= 0:
            copied_file["patch"] = ""
        elif len(patch) > remaining_diff_chars:
            copied_file["patch"] = patch[:remaining_diff_chars]

        consumed_diff_chars += len(copied_file.get("patch") or "")
        scoped_files.append(copied_file)

    return scoped_files


def _limit_repository_file_contents(repository_file_contents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return repository_file_contents[:MAX_REPOSITORY_FILES]


def _analyze_changed_file(file_data: dict[str, Any]) -> dict[str, Any]:
    path = file_data.get("filename", "")
    patch = file_data.get("patch") or ""
    symbols = _extract_symbols(path, patch)
    domains = _extract_domains(path, patch)
    risk_signals = _extract_risk_signals(path, patch)
    removed_access_control = _detect_removed_access_control(patch)

    # 인가 가드가 삭제됐다면 도메인/위험 신호를 보강해 위험 평가 단계가
    # 보안 영향을 인지할 수 있도록 한다.
    if removed_access_control:
        domains = _dedupe([*domains, "authorization"])
        risk_signals = _dedupe([*risk_signals, "security", "access_control"])

    # tree-sitter AST 기반 데이터플로우 관찰. content(head 전체 본문)가 있으면 고신뢰로,
    # 없으면 patch 재구성으로 동작한다(미지원 언어/문법 부재 시 빈 결과).
    structure = analyze_changed_file_structure(path, patch, file_data.get("content"))
    dead_parameters = structure["dead_parameters"]
    shared_mutable_fields = structure["shared_mutable_fields"]
    if dead_parameters:
        risk_signals = _dedupe([*risk_signals, "dead_parameter"])
    if shared_mutable_fields:
        risk_signals = _dedupe([*risk_signals, "shared_mutable_state"])
        domains = _dedupe([*domains, "concurrency"])

    return {
        "path": path,
        "status": file_data.get("status"),
        "change_type": _classify_change_type(path, patch),
        "symbols": symbols,
        "impact_domains": domains,
        "risk_signals": risk_signals,
        "removed_access_control": removed_access_control,
        "dead_parameters": dead_parameters,
        "shared_mutable_fields": shared_mutable_fields,
        "diff_summary": _build_diff_summary(path, file_data, symbols, dead_parameters, shared_mutable_fields),
        "diff_snippet": _mask_secrets(_extract_diff_snippet(patch)),
    }


def _detect_removed_access_control(patch: str) -> list[str]:
    """diff에서 삭제된('-') 줄 중 인가/소유권 가드로 보이는 라인을 수집한다."""
    removed = []
    for line in patch.splitlines():
        if not line.startswith("-") or line.startswith("---"):
            continue
        body = line[1:].strip()
        lowered = body.lower()
        has_access_term = any(term in lowered for term in ACCESS_CONTROL_TERMS)
        has_guard_token = any(token in lowered for token in GUARD_TOKENS)
        if has_access_term and has_guard_token:
            removed.append(body)
    return removed


def _classify_change_type(path: str, patch: str) -> str:
    lowered_path = path.lower()
    suffix = Path(path).suffix.lower()

    if _is_test_file(path):
        return "test_change"
    if suffix in DOC_EXTENSIONS or "docs/" in lowered_path or "readme" in lowered_path:
        return "docs_change"
    if Path(path).name.lower() in CONFIG_FILES or suffix in {".yml", ".yaml", ".toml", ".json"}:
        return "config_change"
    if _extract_affected_apis([{"patch": patch}]):
        return "api_change"
    return "logic_change"


def _extract_symbols(path: str, patch: str) -> list[str]:
    symbols = set()
    stem = Path(path).stem
    if stem:
        symbols.add(stem)

    for pattern in SYMBOL_PATTERNS:
        symbols.update(match.group(1) for match in pattern.finditer(patch))

    return sorted(symbol for symbol in symbols if not _is_noise_symbol(symbol))


def _is_noise_symbol(symbol: str) -> bool:
    """언어 키워드나 표준 예외/타입성 토큰을 심볼에서 제외한다."""
    if symbol.lower() in SYMBOL_STOP_TOKENS:
        return True
    if re.search(r"(?:Exception|Error)$", symbol):
        return True
    return False


def _extract_domains(path: str, patch: str) -> list[str]:
    text = f"{path}\n{patch}".lower()
    return [
        domain
        for domain, keywords in DOMAIN_KEYWORDS.items()
        if any(keyword in text for keyword in keywords)
    ]


def _extract_risk_signals(path: str, patch: str) -> list[str]:
    text = f"{path}\n{patch}".lower()
    return [
        signal
        for signal, keywords in RISK_SIGNAL_KEYWORDS.items()
        if any(keyword in text for keyword in keywords)
    ]


def _extract_affected_apis(changed_files: list[dict[str, Any]]) -> list[dict[str, str]]:
    apis = []
    route_patterns = (
        re.compile(r'["\'](/[^"\']+)["\']'),
        re.compile(r"@(Get|Post|Put|Delete|Patch)Mapping\(([^)]*)\)", re.IGNORECASE),
    )

    for file_data in changed_files:
        patch = file_data.get("patch") or ""
        for pattern in route_patterns:
            for match in pattern.finditer(patch):
                value = match.group(2) if len(match.groups()) >= 2 else match.group(1)
                apis.append({
                    "path": value.strip("\"' "),
                    "reason": f"{file_data.get('filename', '')} diff에서 API path 후보로 감지됨",
                })

    return _dedupe_dicts(apis, "path")


def _find_related_files(
    analyzed_files: list[dict[str, Any]],
    repo_tree: list[str],
    search_terms: list[str],
) -> list[dict[str, str]]:
    related_files = []
    changed_paths = {file_data["path"] for file_data in analyzed_files}

    for path in repo_tree:
        if path in changed_paths:
            continue

        lowered_path = path.lower()
        matched_terms = [term for term in search_terms if term and term in lowered_path]
        if matched_terms:
            related_files.append({
                "file": path,
                "reason": f"파일 경로가 변경 symbol/path 키워드와 일치함: {', '.join(matched_terms[:3])}",
                "retrieval_source": "path_rule",
            })

    return related_files[:10]


def _extract_imports(changed_files: list[dict[str, Any]]) -> list[dict[str, str]]:
    imports = []

    for file_data in changed_files:
        path = file_data.get("filename", "")
        patch = file_data.get("patch") or ""
        for pattern in IMPORT_PATTERNS:
            for match in pattern.finditer(patch):
                imported_value = ".".join(part for part in match.groups() if part).strip()
                imports.append({
                    "file": path,
                    "import": imported_value,
                })

    return _dedupe_dicts(imports, "import")


def _find_import_related_files(
    imports: list[dict[str, str]],
    repo_tree: list[str],
) -> list[dict[str, str]]:
    related_files = []

    for import_data in imports:
        import_path = import_data["import"].replace(".", "/").replace("*", "").strip("/")
        if not import_path:
            continue

        for path in repo_tree:
            lowered_path = path.lower()
            lowered_import = import_path.lower()
            if lowered_import in lowered_path or Path(path).stem.lower() in lowered_import:
                related_files.append({
                    "file": path,
                    "reason": f"import 경로와 repository path가 일치함: {import_data['import']}",
                    "retrieval_source": "import_path_rule",
                    "matched_terms": [import_data["import"]],
                })

    return _dedupe_dicts(related_files, "file")[:10]


def _get_repository_file_contents(pr_data: dict[str, Any]) -> list[dict[str, Any]]:
    return pr_data.get("repository_file_contents") or pr_data.get("related_file_contents") or []


def _exclude_changed_files(
    repository_file_contents: list[dict[str, Any]],
    analyzed_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """변경 파일 자신은 retrieval 코퍼스에서 제외한다(자기-매칭 노이즈 방지)."""
    changed_paths = {file_data["path"] for file_data in analyzed_files}
    return [
        file_data
        for file_data in repository_file_contents
        if (file_data.get("path") or file_data.get("filename")) not in changed_paths
    ]


def _search_related_contents(
    search_terms: list[str],
    repository_file_contents: list[dict[str, Any]],
    limit: int = 10,
) -> list[dict[str, Any]]:
    documents = _build_content_chunks(repository_file_contents)
    query_tokens = _tokenize(" ".join(search_terms))

    if not documents or not query_tokens:
        return []

    bm25 = BM25Okapi([document["tokens"] for document in documents])
    scores = bm25.get_scores(query_tokens)
    scored_documents = sorted(
        zip(documents, scores),
        key=lambda item: item[1],
        reverse=True,
    )
    results = []

    for document, score in scored_documents:
        matched_terms = _matched_terms(search_terms, document["text"])
        if not matched_terms:
            continue

        results.append({
            "file": document["path"],
            "score": round(float(score), 4),
            "matched_terms": matched_terms,
            "start_line": document["start_line"],
            "end_line": document["end_line"],
            "snippet": document["text"],
            "reason": f"BM25/keyword 검색에서 관련 snippet으로 감지됨: {', '.join(matched_terms[:5])}",
            "retrieval_source": "bm25",
        })

        if len(results) >= limit:
            break

    return results


def _looks_like_call(line: str, symbol: str) -> bool:
    """심볼이 호출부로 쓰였는지(정의/생성자는 제외) 판단한다."""
    escaped = re.escape(symbol)
    if re.search(rf"\bnew\s+{escaped}\s*\(", line):  # 생성자: new Symbol(...)
        return False
    if re.search(rf"\b(?:def|function|class)\s+{escaped}\b", line):  # 정의부
        return False
    return True


def _find_direct_callers(
    analyzed_files: list[dict[str, Any]],
    repository_file_contents: list[dict[str, Any]],
    limit: int = 10,
) -> list[dict[str, Any]]:
    symbols = _dedupe(
        symbol
        for file_data in analyzed_files
        for symbol in file_data["symbols"]
        if len(symbol) > 2
    )
    callers = []

    if not symbols:
        return callers

    for file_data in repository_file_contents:
        path = file_data.get("path") or file_data.get("filename")
        content = file_data.get("content") or ""
        if not path or not content:
            continue

        lines = content.splitlines()
        for index, line in enumerate(lines):
            matched_symbols = [
                symbol
                for symbol in symbols
                if re.search(rf"\b{re.escape(symbol)}\s*\(", line)
                and _looks_like_call(line, symbol)
            ]
            if not matched_symbols:
                continue

            start = max(index - 5, 0)
            end = min(index + 6, len(lines))
            callers.append({
                "file": path,
                "score": 0.0,
                "matched_terms": matched_symbols,
                "start_line": start + 1,
                "end_line": end,
                "snippet": _mask_secrets("\n".join(lines[start:end])),
                "reason": f"변경 symbol 호출부 후보로 감지됨: {', '.join(matched_symbols[:3])}",
                "retrieval_source": "symbol_exact_match",
            })

            if len(callers) >= limit:
                return callers

    return callers


def _enrich_evidence_candidates_with_llm(
    analyzed_files: list[dict[str, Any]],
    evidence_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not evidence_candidates:
        return evidence_candidates

    payload = _build_evidence_payload(analyzed_files, evidence_candidates)
    response = invoke_solar(EVIDENCE_SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False, indent=2))
    if response is None:
        return evidence_candidates

    parsed = _parse_json_object(response)
    if parsed is None:
        return evidence_candidates

    llm_items = parsed.get("items")
    if not isinstance(llm_items, list):
        return evidence_candidates

    enriched_candidates = [dict(candidate) for candidate in evidence_candidates]
    for item in llm_items:
        if not isinstance(item, dict):
            continue

        index = item.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(enriched_candidates):
            continue

        llm_evidence = _normalize_llm_evidence(item)
        if not llm_evidence:
            continue

        enriched_candidates[index]["llm_evidence"] = llm_evidence
        enriched_candidates[index]["reason"] = llm_evidence["evidence_reason"]

    return enriched_candidates


def _build_evidence_payload(
    analyzed_files: list[dict[str, Any]],
    evidence_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "changed_files": [
            {
                "path": file_data["path"],
                "change_type": file_data["change_type"],
                "symbols": file_data["symbols"],
                "impact_domains": file_data["impact_domains"],
                "risk_signals": file_data["risk_signals"],
                "diff_summary": file_data["diff_summary"],
                "diff_snippet": file_data["diff_snippet"],
            }
            for file_data in analyzed_files
        ],
        "evidence_candidates": [
            {
                "index": index,
                "file": candidate.get("file"),
                "retrieval_source": candidate.get("retrieval_source"),
                "matched_terms": candidate.get("matched_terms", []),
                "start_line": candidate.get("start_line"),
                "end_line": candidate.get("end_line"),
                "snippet": _truncate_text(candidate.get("snippet", ""), MAX_LLM_SNIPPET_CHARS),
                "rule_reason": candidate.get("reason"),
            }
            for index, candidate in enumerate(evidence_candidates[:MAX_LLM_EVIDENCE_ITEMS])
        ],
        "instruction": "각 evidence candidate가 changed_files의 변경 근거로 얼마나 관련 있는지와 그 이유만 판단하세요.",
    }


def _normalize_llm_evidence(item: dict[str, Any]) -> dict[str, Any] | None:
    relevance = item.get("relevance")
    if relevance not in {"high", "medium", "low"}:
        relevance = "medium"

    evidence_reason = item.get("evidence_reason")
    if not isinstance(evidence_reason, str) or not evidence_reason.strip():
        return None

    supported_claims = item.get("supported_claims")
    if not isinstance(supported_claims, list):
        supported_claims = []

    uncertainty = item.get("uncertainty")
    if not isinstance(uncertainty, str):
        uncertainty = ""

    return {
        "relevance": relevance,
        "evidence_reason": _mask_secrets(evidence_reason.strip()),
        "supported_claims": [
            _mask_secrets(str(claim))
            for claim in supported_claims
            if str(claim).strip()
        ][:5],
        "uncertainty": _mask_secrets(uncertainty.strip()),
    }


def _build_content_chunks(repository_file_contents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    documents = []

    for file_data in repository_file_contents:
        path = file_data.get("path") or file_data.get("filename")
        content = file_data.get("content") or ""
        if not path or not content:
            continue

        lines = content.splitlines()
        if not lines:
            continue

        step = max(CHUNK_SIZE - CHUNK_OVERLAP, 1)
        for start in range(0, len(lines), step):
            chunk_lines = lines[start:start + CHUNK_SIZE]
            if not chunk_lines:
                continue
            chunk_text = "\n".join(chunk_lines)
            tokens = _tokenize(chunk_text)
            if not tokens:
                continue

            documents.append({
                "path": path,
                "start_line": start + 1,
                "end_line": start + len(chunk_lines),
                "text": _mask_secrets(chunk_text),
                "tokens": tokens,
            })

            if len(documents) >= MAX_CONTENT_CHUNKS:
                return documents

    return documents


def _merge_related_files(
    path_related_files: list[dict[str, Any]],
    content_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = {item["file"]: dict(item) for item in path_related_files}

    for result in content_results:
        path = result["file"]
        if path in merged:
            merged[path]["reason"] = f"{merged[path]['reason']} / {result['reason']}"
            merged[path]["retrieval_source"] = _merge_retrieval_sources(
                merged[path].get("retrieval_source"),
                result["retrieval_source"],
            )
            merged[path]["matched_terms"] = _dedupe([
                *merged[path].get("matched_terms", []),
                *result["matched_terms"],
            ])

            existing_score = float(merged[path].get("score") or 0.0)
            result_score = float(result.get("score") or 0.0)
            if result_score >= existing_score or "snippet" not in merged[path]:
                merged[path]["snippet"] = result["snippet"]
                merged[path]["start_line"] = result["start_line"]
                merged[path]["end_line"] = result["end_line"]
            merged[path]["score"] = max(existing_score, result_score)
            continue

        merged[path] = {
            "file": path,
            "reason": result["reason"],
            "retrieval_source": result["retrieval_source"],
            "matched_terms": result["matched_terms"],
            "score": result["score"],
            "snippet": result["snippet"],
            "start_line": result["start_line"],
            "end_line": result["end_line"],
        }

    return list(merged.values())[:10]


def _merge_retrieval_sources(existing_source: str | None, new_source: str) -> str:
    sources = []
    for source in (existing_source or "", new_source):
        sources.extend(item for item in source.split("+") if item)
    return "+".join(_dedupe(sources))


def _build_search_terms(
    analyzed_files: list[dict[str, Any]],
    pr_data: dict[str, Any],
    imports: list[dict[str, str]],
) -> list[str]:
    # 고신호(심볼/도메인)를 일반 토큰(경로/메타데이터)보다 앞에 배치해 우선순위를 준다.
    high_signal = []
    low_signal = []
    for file_data in analyzed_files:
        high_signal.extend(symbol.lower() for symbol in file_data["symbols"])
        high_signal.extend(domain.lower() for domain in file_data["impact_domains"])
        path = Path(file_data["path"])
        low_signal.extend(
            part.lower()
            for part in path.parts
            if len(part) > 2 and not _is_stop_term(part.lower())
        )

    metadata_text = " ".join(
        str(value)
        for value in [
            pr_data.get("title", ""),
            pr_data.get("body", ""),
            " ".join(_get_commit_messages(pr_data)),
            " ".join(import_data["import"] for import_data in imports),
        ]
    )
    low_signal.extend(
        token for token in _tokenize(metadata_text)
        if len(token) > 2 and not _is_stop_term(token)
    )
    return _dedupe([*high_signal, *low_signal])


def _is_stop_term(term: str) -> bool:
    return term in PATH_STOP_TERMS or term in ENGLISH_STOP_WORDS


def _build_summary(pr_data: dict[str, Any], analyzed_files: list[dict[str, Any]], domains: list[str]) -> str:
    title = _mask_secrets(str(pr_data.get("title") or ""))
    if title:
        return f"'{title}' PR은 {len(analyzed_files)}개 파일을 변경하며, 영향 도메인은 {', '.join(domains) or '미분류'}로 추정됩니다."
    return f"{len(analyzed_files)}개 파일 변경을 분석했으며, 영향 도메인은 {', '.join(domains) or '미분류'}로 추정됩니다."


def _infer_change_intent(pr_data: dict[str, Any], analyzed_files: list[dict[str, Any]]) -> str:
    title = str(pr_data.get("title") or "").strip()
    body = str(pr_data.get("body") or "").strip()
    commit_messages = _get_commit_messages(pr_data)

    if title:
        return _mask_secrets(title)
    if body:
        return _mask_secrets(body.splitlines()[0][:200])
    if commit_messages:
        return _mask_secrets(commit_messages[0])

    change_types = _dedupe(file_data["change_type"] for file_data in analyzed_files)
    if change_types:
        return f"{', '.join(change_types)} 유형의 변경으로 추정됩니다."
    return "PR 메타데이터가 부족하여 변경 의도를 명확히 추정하기 어렵습니다."


def _infer_pr_change_type(pr_data: dict[str, Any], analyzed_files: list[dict[str, Any]]) -> str:
    metadata = " ".join([str(pr_data.get("title") or ""), *_get_commit_messages(pr_data)]).lower()
    conventional_match = re.search(r"\b(feat|fix|refactor|chore|docs|test|style|perf|ci|build)(?:\([^)]+\))?:", metadata)
    if conventional_match:
        return conventional_match.group(1)

    file_change_types = [file_data["change_type"] for file_data in analyzed_files]
    if file_change_types and all(change_type == "docs_change" for change_type in file_change_types):
        return "docs"
    if file_change_types and all(change_type == "test_change" for change_type in file_change_types):
        return "test"
    if "api_change" in file_change_types or "logic_change" in file_change_types:
        return "feat_or_fix"
    if "config_change" in file_change_types:
        return "chore"
    return "unknown"


def _build_main_changes(analyzed_files: list[dict[str, Any]]) -> list[str]:
    return [
        _mask_secrets(file_data["diff_summary"])
        for file_data in analyzed_files[:10]
    ]


def _get_commit_messages(pr_data: dict[str, Any]) -> list[str]:
    messages = pr_data.get("commit_messages") or pr_data.get("commits") or []
    if not isinstance(messages, list):
        return []

    result = []
    for item in messages:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            result.append(str(item.get("message") or item.get("commit", {}).get("message") or ""))

    return [message for message in result if message]


def _build_comment_notice(limitation: dict[str, Any]) -> str | None:
    if not limitation["limited"]:
        return None
    return "대규모 PR로 인해 주요 파일과 snippet 중심으로 분석되었습니다."


def _build_diff_summary(
    path: str,
    file_data: dict[str, Any],
    symbols: list[str],
    dead_parameters: list[dict[str, Any]] | None = None,
    shared_mutable_fields: list[dict[str, Any]] | None = None,
) -> str:
    additions = int(file_data.get("additions") or 0)
    deletions = int(file_data.get("deletions") or 0)
    symbol_text = f" 주요 symbol 후보: {', '.join(symbols[:5])}." if symbols else ""
    # AST 관찰을 요약에 접합 → main_changes/evidence 경유로 다운스트림(checklist)까지 전달된다.
    observation_text = "".join(
        f" [구조 관찰] {item['evidence']}"
        for item in [*(dead_parameters or []), *(shared_mutable_fields or [])]
    )
    return _mask_secrets(
        f"{path}에서 {additions}줄 추가, {deletions}줄 삭제가 발생했습니다.{symbol_text}{observation_text}"
    )


def _extract_diff_snippet(patch: str, max_lines: int = 8) -> str:
    if not patch:
        return ""

    changed_lines = [
        line
        for line in patch.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    return "\n".join(changed_lines[:max_lines])


def _build_evidence(
    analyzed_files: list[dict[str, Any]],
    content_results: list[dict[str, Any]],
) -> list[dict[str, str]]:
    evidence = []
    for file_data in analyzed_files:
        evidence.append({
            "file": file_data["path"],
            "symbol": ", ".join(file_data["symbols"][:3]),
            "summary": file_data["diff_summary"],
            "snippet": file_data["diff_snippet"],
        })

    for result in content_results[:5]:
        evidence_item = {
            "file": result["file"],
            "symbol": ", ".join(result["matched_terms"][:3]),
            "summary": result["reason"],
            "snippet": result["snippet"],
        }
        if "llm_evidence" in result:
            evidence_item["llm_evidence"] = result["llm_evidence"]
        evidence.append(evidence_item)
    return evidence


def _build_missing_test_concerns(risk_signals: list[str], related_tests: list[str]) -> list[str]:
    if related_tests:
        return []

    concerns = []
    if any(signal in risk_signals for signal in ("auth", "security")):
        concerns.append("인증/보안 변경에 대한 관련 테스트 확인이 필요합니다.")
    if "database" in risk_signals:
        concerns.append("데이터 정합성 및 migration 관련 테스트 확인이 필요합니다.")
    if "public_api" in risk_signals:
        concerns.append("public API 요청/응답 회귀 테스트 확인이 필요합니다.")
    return concerns


def _is_test_file(path: str) -> bool:
    lowered_path = path.lower()
    return any(keyword in lowered_path for keyword in TEST_KEYWORDS)


def _tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)]


def _matched_terms(search_terms: list[str], text: str) -> list[str]:
    lowered_text = text.lower()
    return [term for term in search_terms if term and term.lower() in lowered_text]


def _mask_secrets(text: str) -> str:
    masked_text = text
    for pattern in SECRET_PATTERNS:
        def replacement(match: re.Match[str]) -> str:
            if len(match.groups()) >= 5:
                return f"{match.group(1)}{match.group(2)}{match.group(3)}[MASKED_SECRET]{match.group(5)}"
            return "[MASKED_SECRET]"

        masked_text = pattern.sub(replacement, masked_text)
    return masked_text


def _parse_json_object(content: str) -> dict[str, Any] | None:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match is None:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    return parsed if isinstance(parsed, dict) else None


def _truncate_text(text: str, max_chars: int) -> str:
    masked_text = _mask_secrets(str(text))
    if len(masked_text) <= max_chars:
        return masked_text
    return f"{masked_text[:max_chars]}...[truncated]"


def _dedupe(items: list[str] | Any) -> list[str]:
    result = []
    seen = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _dedupe_dicts(items: list[dict[str, str]], key: str) -> list[dict[str, str]]:
    result = []
    seen = set()
    for item in items:
        value = item.get(key)
        if value and value not in seen:
            seen.add(value)
            result.append(item)
    return result
