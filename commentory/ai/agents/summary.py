import json
import re
from typing import Any, Dict, Mapping, Optional

try:
    from agents.llm import invoke_solar
except ModuleNotFoundError:
    from commentory.ai.agents.llm import invoke_solar

try:
    from state import PRState
except ImportError:  # pragma: no cover - lets this module be imported standalone.
    PRState = Dict[str, Any]  # type: ignore


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
- ImpactContext에 없는 정보는 추가하지 않습니다.
- ImpactContext에 명시된 pr_summary, diff_summary, evidence, symbols, impact_scope만 근거로 사용합니다.
- 출력은 JSON 객체 하나만 반환합니다.

JSON 스키마:
{
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
    impact_context = state.get("impact_context")
    if not impact_context:
        raise ValueError("summary_agent requires impact_context.")

    response = invoke_solar(SYSTEM_PROMPT, _build_user_prompt(state))
    if response is None:
        raise RuntimeError("Solar API key is not configured or Solar response is empty.")

    parsed = _parse_json_object(response)
    if parsed is None:
        raise ValueError("Solar response is not a valid JSON object.")

    return {**state, "summary_result": _normalize_summary_result(parsed)}


def _build_user_prompt(state: Mapping[str, Any]) -> str:
    payload = {
        "pr_data": state.get("pr_data"),
        "impact_context": state.get("impact_context"),
    }
    return (
        "아래 PR 데이터와 ImpactContext를 기반으로 PR 요약 JSON을 생성하세요.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _parse_json_object(content: str) -> Optional[Dict[str, Any]]:
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


def _normalize_summary_result(result: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "markdown": result.get("markdown") if isinstance(result.get("markdown"), str) else "",
    }
