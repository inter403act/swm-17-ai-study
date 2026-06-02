import json
import os
import re
from typing import Any, Dict, List, Mapping, Optional

try:
    from state import PRState
except ImportError:  # pragma: no cover - lets this module be imported standalone.
    PRState = Dict[str, Any]  # type: ignore


def _load_env_file() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    load_dotenv()


_load_env_file()

SOLAR_API_KEY = os.getenv("SOLAR_API_KEY") or os.getenv("UPSTAGE_API_KEY") or "{SOLAR_API_KEY}"
SOLAR_MODEL = os.getenv("SOLAR_MODEL", "solar-pro2")

MAX_CONTEXT_CHARS = 14000
MAX_SNIPPET_CHARS = 600
MAX_MARKDOWN_CHARS = 5000

SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s,}]+"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[a-z0-9._\-]+"),
    re.compile(r"(?i)(ghp|github_pat|sk|live)_[a-z0-9_]{20,}"),
)


SYSTEM_PROMPT = """당신은 Commentory의 PR 요약 Agent입니다.

역할:
- ImpactContext만 근거로 GitHub PR comment에 넣을 수 있는 요약 markdown 데이터를 생성합니다.
- 리뷰어가 빠르게 읽고 바로 활용할 수 있도록 정확하고 간결한 실무형 문장으로 작성합니다.

반드시 생성할 내용:
1. 한줄 요약
2. 주요 변경 사항
3. 영향도 및 API 스코프

제한:
- 위험도 등급, 라우팅 판단, 리뷰 체크리스트, 승인/거절 의견은 생성하지 않습니다.
- ImpactContext에 없는 사실은 단정하지 않습니다. ImpactContext에 명시된 diff_summary/evidence/symbols만 사용합니다.
- 불확실한 내용은 "추정됨", "연결됨", "영향 가능"처럼 근거 기반 표현을 사용합니다.
- 출력은 JSON 객체 하나만 반환합니다.

JSON 스키마:
{
  "summary": "한줄 요약 문자열",
  "main_changes": ["주요 변경 사항"],
  "impact_and_api_scope": {
    "domains": ["영향 도메인"],
    "affected_apis": [
      {"path": "API path", "reason": "영향 사유"}
    ],
    "related_files": ["관련 파일"],
    "affected_tests": ["관련 테스트"]
  },
  "markdown": "PR comment에 바로 넣을 수 있는 markdown 문자열"
}

markdown 형식:
### 요약
- ...

### 주요 변경 사항
- ...

### 영향도 및 API 스코프
- 영향 도메인: ...
- API: `path` - reason
- 관련 파일: ...
- 관련 테스트: ...
"""


def summary_agent(state: PRState) -> PRState:
    """LangGraph node that adds PR summary markdown data to state."""
    impact_context = _as_dict(state.get("impact_context"))
    pr_data = _as_dict(state.get("pr_data"))

    if not impact_context:
        return {
            **state,
            "summary_result": _empty_result("ImpactContext가 없어 PR 요약을 생성할 수 없습니다."),
        }

    context_payload = _build_context_payload(pr_data, impact_context)
    result = _generate_with_solar(context_payload)

    if result is None:
        result = _fallback_summary(impact_context, pr_data)

    normalized = _normalize_result(result, impact_context)
    return {**state, "summary_result": normalized}


def generate_summary(state: PRState) -> PRState:
    """Alias for graph builders that prefer an explicit verb name."""
    return summary_agent(state)


def summarize_pr(state: PRState) -> PRState:
    """Alias for graph builders that use PR-oriented node names."""
    return summary_agent(state)


def run(state: PRState) -> PRState:
    """Small convenience alias for manual node execution."""
    return summary_agent(state)


def _generate_with_solar(context_payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if not SOLAR_API_KEY or SOLAR_API_KEY == "{SOLAR_API_KEY}":
        return None

    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_upstage import ChatUpstage

    llm = ChatUpstage(upstage_api_key=SOLAR_API_KEY, model_name=SOLAR_MODEL, temperature=0)
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=(
                "아래 PR 데이터와 ImpactContext를 기반으로 PR 요약 JSON을 생성하세요.\n\n"
                f"{json.dumps(context_payload, ensure_ascii=False, indent=2)}"
            )
        ),
    ]
    response = llm.invoke(messages)
    return _parse_json_object(str(response.content))


def _build_context_payload(pr_data: Mapping[str, Any], impact_context: Mapping[str, Any]) -> Dict[str, Any]:
    payload = {
        "pr_data": {
            "title": pr_data.get("title"),
            "body": pr_data.get("body"),
            "changed_files": pr_data.get("changed_files"),
        },
        "impact_context": impact_context,
    }

    if len(json.dumps(payload, ensure_ascii=False, default=str)) > MAX_CONTEXT_CHARS:
        payload = {
            "pr_data": payload["pr_data"],
            "impact_context": _compact_impact_context(impact_context),
            "context_note": "분석 입력이 길어 diff snippet 등 일부 긴 내용은 생략되었습니다.",
        }

    return json.loads(_mask_secrets(json.dumps(payload, ensure_ascii=False, default=str)))


def _normalize_result(result: Mapping[str, Any], impact_context: Mapping[str, Any]) -> Dict[str, Any]:
    summary = _clean_text(result.get("summary")) or _fallback_one_line(impact_context)
    main_changes = _clean_list(result.get("main_changes")) or _extract_main_changes(impact_context)

    scope = _as_dict(result.get("impact_and_api_scope"))
    impact_scope = _as_dict(impact_context.get("impact_scope"))

    normalized_scope = {
        "domains": _clean_list(scope.get("domains")) or _clean_list(impact_scope.get("domains")),
        "affected_apis": _clean_api_items(scope.get("affected_apis"))
        or _clean_api_items(impact_scope.get("affected_apis")),
        "related_files": _clean_list(scope.get("related_files")) or _clean_list(impact_scope.get("related_files")),
        "affected_tests": _clean_list(scope.get("affected_tests")) or _clean_list(impact_scope.get("affected_tests")),
    }

    markdown = _clean_markdown(result.get("markdown"))
    if not markdown:
        markdown = _build_markdown(summary, main_changes, normalized_scope)

    return {
        "summary": summary,
        "main_changes": main_changes,
        "impact_and_api_scope": normalized_scope,
        "markdown": markdown,
    }


def _fallback_summary(impact_context: Mapping[str, Any], pr_data: Mapping[str, Any]) -> Dict[str, Any]:
    summary = _fallback_one_line(impact_context, pr_data)
    main_changes = _extract_main_changes(impact_context)
    impact_scope = _as_dict(impact_context.get("impact_scope"))
    scope = {
        "domains": _clean_list(impact_scope.get("domains")),
        "affected_apis": _clean_api_items(impact_scope.get("affected_apis")),
        "related_files": _clean_list(impact_scope.get("related_files")),
        "affected_tests": _clean_list(impact_scope.get("affected_tests")),
    }

    return {
        "summary": summary,
        "main_changes": main_changes,
        "impact_and_api_scope": scope,
        "markdown": _build_markdown(summary, main_changes, scope),
    }


def _compact_impact_context(impact_context: Mapping[str, Any]) -> Dict[str, Any]:
    compact = {
        "pr_summary": impact_context.get("pr_summary"),
        "change_stats": impact_context.get("change_stats"),
        "impact_scope": impact_context.get("impact_scope"),
        "dependency_context": impact_context.get("dependency_context"),
        "test_coverage_signal": impact_context.get("test_coverage_signal"),
        "changed_files": [],
        "evidence": [],
    }

    for file_change in _as_list(impact_context.get("changed_files"))[:10]:
        item = _as_dict(file_change)
        compact["changed_files"].append(
            {
                "path": item.get("path"),
                "change_type": item.get("change_type"),
                "symbols": item.get("symbols"),
                "diff_summary": item.get("diff_summary"),
                "diff_snippet": _clean_text(item.get("diff_snippet")),
            }
        )

    for evidence in _as_list(impact_context.get("evidence"))[:10]:
        item = _as_dict(evidence)
        compact["evidence"].append(
            {
                "file": item.get("file"),
                "symbol": item.get("symbol"),
                "summary": item.get("summary"),
                "snippet": _clean_text(item.get("snippet")),
            }
        )

    return compact


def _fallback_one_line(impact_context: Mapping[str, Any], pr_data: Optional[Mapping[str, Any]] = None) -> str:
    pr_summary = _clean_text(impact_context.get("pr_summary"))
    if pr_summary:
        return pr_summary

    title = _clean_text((pr_data or {}).get("title"))
    if title:
        return f"이 PR은 '{title}' 변경을 포함합니다."

    changed_files = _clean_list(impact_context.get("changed_files"))
    if changed_files:
        return f"이 PR은 {len(changed_files)}개 파일의 변경을 포함합니다."

    return "이 PR은 ImpactContext에 요약 가능한 변경 내용을 포함합니다."


def _extract_main_changes(impact_context: Mapping[str, Any]) -> List[str]:
    changes: List[str] = []
    for file_change in _as_list(impact_context.get("changed_files")):
        item = _as_dict(file_change)
        path = _clean_text(item.get("path"))
        diff_summary = _clean_text(item.get("diff_summary"))
        symbols = _clean_list(item.get("symbols"))

        if path and diff_summary:
            changes.append(f"`{path}`: {diff_summary}")
        elif path and symbols:
            changes.append(f"`{path}`: {', '.join(symbols)} 관련 변경")
        elif path:
            changes.append(f"`{path}` 변경")

    evidence_changes = []
    for evidence in _as_list(impact_context.get("evidence")):
        item = _as_dict(evidence)
        summary = _clean_text(item.get("summary"))
        file_path = _clean_text(item.get("file"))
        if summary and file_path:
            evidence_changes.append(f"`{file_path}`: {summary}")
        elif summary:
            evidence_changes.append(summary)

    for change in evidence_changes:
        if change not in changes:
            changes.append(change)

    if not changes:
        pr_summary = _clean_text(impact_context.get("pr_summary"))
        if pr_summary:
            changes.append(pr_summary)

    return changes[:5]


def _build_markdown(summary: str, main_changes: List[str], scope: Mapping[str, Any]) -> str:
    lines = [
        "### 요약",
        f"- {summary}",
        "",
        "### 주요 변경 사항",
    ]

    lines.extend(f"- {item}" for item in (main_changes or ["ImpactContext 기준 주요 변경 사항이 명시되지 않았습니다."]))
    lines.extend(["", "### 영향도 및 API 스코프"])

    domains = _clean_list(scope.get("domains"))
    apis = _clean_api_items(scope.get("affected_apis"))
    related_files = _clean_list(scope.get("related_files"))
    affected_tests = _clean_list(scope.get("affected_tests"))

    lines.append(f"- 영향 도메인: {', '.join(domains) if domains else '명시된 도메인 없음'}")

    if apis:
        for api in apis:
            lines.append(f"- API: `{api['path']}` - {api['reason']}")
    else:
        lines.append("- API: 명시된 API 영향 없음")

    lines.append(f"- 관련 파일: {', '.join(f'`{item}`' for item in related_files) if related_files else '명시된 관련 파일 없음'}")
    lines.append(f"- 관련 테스트: {', '.join(f'`{item}`' for item in affected_tests) if affected_tests else '명시된 관련 테스트 없음'}")
    return "\n".join(lines)


def _empty_result(message: str) -> Dict[str, Any]:
    return {
        "summary": message,
        "main_changes": [],
        "impact_and_api_scope": {
            "domains": [],
            "affected_apis": [],
            "related_files": [],
            "affected_tests": [],
        },
        "markdown": _build_markdown(message, [], {}),
    }


def _parse_json_object(content: str) -> Optional[Dict[str, Any]]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    return parsed if isinstance(parsed, dict) else None


def _mask_secrets(text: str) -> str:
    masked = text
    for pattern in SECRET_PATTERNS:
        masked = pattern.sub(lambda match: _mask_match(match), masked)
    return masked


def _mask_match(match: re.Match[str]) -> str:
    if match.lastindex:
        return f"{match.group(1)}[MASKED]"
    return "[MASKED]"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return _mask_secrets(text[:MAX_SNIPPET_CHARS])


def _clean_markdown(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return _mask_secrets(text[:MAX_MARKDOWN_CHARS])


def _clean_list(value: Any) -> List[str]:
    items: List[str] = []
    for item in _as_list(value):
        if isinstance(item, Mapping):
            text = _clean_text(item.get("path") or item.get("file") or item.get("name") or item.get("summary"))
        else:
            text = _clean_text(item)
        if text and text not in items:
            items.append(text)
    return items


def _clean_api_items(value: Any) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for item in _as_list(value):
        api = _as_dict(item)
        path = _clean_text(api.get("path"))
        reason = _clean_text(api.get("reason")) or "ImpactContext에서 영향 API로 식별됨"
        if path and not any(existing["path"] == path for existing in items):
            items.append({"path": path, "reason": reason})
    return items


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]
