import re
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Okapi

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
    content_results = _search_related_contents(
        search_terms,
        _limit_repository_file_contents(_get_repository_file_contents(pr_data)),
    )
    direct_callers = _find_direct_callers(analyzed_files, _limit_repository_file_contents(_get_repository_file_contents(pr_data)))
    related_files = _merge_related_files(path_related_files + import_related_files, content_results + direct_callers)
    related_tests = [item["file"] for item in related_files if _is_test_file(item["file"])]
    main_changes = _build_main_changes(analyzed_files)

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
        "evidence": _build_evidence(analyzed_files, content_results + direct_callers),
    }


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

    return {
        "path": path,
        "status": file_data.get("status"),
        "change_type": _classify_change_type(path, patch),
        "symbols": symbols,
        "impact_domains": _extract_domains(path, patch),
        "risk_signals": _extract_risk_signals(path, patch),
        "diff_summary": _build_diff_summary(path, file_data, symbols),
        "diff_snippet": _mask_secrets(_extract_diff_snippet(patch)),
    }


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

    return sorted(symbols)


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
    terms = []
    for file_data in analyzed_files:
        path = Path(file_data["path"])
        terms.extend(
            part.lower()
            for part in path.parts
            if len(part) > 2 and part.lower() not in PATH_STOP_TERMS
        )
        terms.extend(symbol.lower() for symbol in file_data["symbols"])
        terms.extend(domain.lower() for domain in file_data["impact_domains"])

    metadata_text = " ".join(
        str(value)
        for value in [
            pr_data.get("title", ""),
            pr_data.get("body", ""),
            " ".join(_get_commit_messages(pr_data)),
            " ".join(import_data["import"] for import_data in imports),
        ]
    )
    terms.extend(token for token in _tokenize(metadata_text) if len(token) > 2 and token not in PATH_STOP_TERMS)
    return _dedupe(terms)


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


def _build_diff_summary(path: str, file_data: dict[str, Any], symbols: list[str]) -> str:
    additions = int(file_data.get("additions") or 0)
    deletions = int(file_data.get("deletions") or 0)
    symbol_text = f" 주요 symbol 후보: {', '.join(symbols[:5])}." if symbols else ""
    return _mask_secrets(f"{path}에서 {additions}줄 추가, {deletions}줄 삭제가 발생했습니다.{symbol_text}")


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
        evidence.append({
            "file": result["file"],
            "symbol": ", ".join(result["matched_terms"][:3]),
            "summary": result["reason"],
            "snippet": result["snippet"],
        })
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
