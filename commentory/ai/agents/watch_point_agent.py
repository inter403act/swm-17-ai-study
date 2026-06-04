"""watch-point 생성 에이전트 — hand-rolled tool 루프.

Phase 2 one-shot과 달리, LLM이 도구(find_references/정의/주변 코드 조회)를 능동적으로
호출해 단계적으로 근거를 모은 뒤 watch-point를 생성한다(점진적 개방).

설계 원칙:
- 도구는 CodeProvider에만 의존(데이터 소스 무지) → 3b에서 provider만 교체.
- 하드 캡(iter/tool 수)으로 비용/지연을 bound.
- 인용 코퍼스 = '에이전트가 실제로 본 것'(도구 결과 + 변경 본문) → Phase 2 인용 검증을
  그대로 재사용해 환각을 폐기.
- 실패(키 없음/예외/JSON 파싱 실패) 시 None → 호출자가 one-shot으로 폴백.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

try:
    from agents.llm import get_solar_chat_model
    from agents.code_tools import build_tools
    from agents.pr_analysis import _mask_secrets, _normalize_ws, _parse_json_object, _validate_watch_points
except ModuleNotFoundError:
    from commentory.ai.agents.llm import get_solar_chat_model
    from commentory.ai.agents.code_tools import build_tools
    from commentory.ai.agents.pr_analysis import _mask_secrets, _normalize_ws, _parse_json_object, _validate_watch_points

MAX_ITERATIONS = 6
MAX_TOOL_CALLS = 12
MAX_SEED_BODY_CHARS = 4000

SYSTEM_PROMPT = """당신은 Commentory PR 분석 Agent 내부의 watch-point 생성 에이전트입니다.
제공된 도구로 코드를 능동적으로 조사해, 리뷰어가 반드시 봐둬야 할 지점(watch-point)을
근거와 함께 만듭니다.

도구:
- find_references(symbol): 메서드 호출부를 찾는다(영향 범위 파악).
- get_symbol_definition(symbol): 메서드/타입 정의 본문을 가져온다.
- get_enclosing_context(path, line): 특정 줄을 감싸는 메서드/클래스를 가져온다.
- read_file_region(path, start_line, end_line): 파일 구간을 읽는다.

원칙:
- anchors(죽은 인자 / 공유 가변 상태 / 삭제된 인가 가드)는 정적 분석으로 확정된 사실입니다.
  이를 출발점으로 "그래서 무엇이 문제가 될 수 있는지"를 도구로 확인해 함의를 연결하세요.
- 위험도 등급(HIGH/MEDIUM/LOW) 판정, 점수, 승인/거절 의견은 내지 마세요. 봐둬야 할 점만.
- 제공된 코드/도구 결과에 없는 사실은 지어내지 마세요.
- 충분히 조사했으면 아래 JSON 객체 하나만 출력하세요. 모든 watch-point에는 도구나 변경
  본문에서 그대로 복사한 실제 코드 한 줄(quote)을 인용하세요. 인용을 붙일 수 없으면 만들지 마세요.

JSON 스키마:
{
  "watch_points": [
    {
      "observation": "리뷰어가 봐둬야 할 핵심을 한 문장으로",
      "reasoning": "변경/anchor로부터 이것이 왜 주의 지점인지의 추론",
      "watch_for": "리뷰어가 구체적으로 확인/검증해야 할 것",
      "citations": [
        {"file": "파일 경로 또는 파일명", "lines": "30-32", "quote": "제공된 코드에서 그대로 복사한 한 줄"}
      ],
      "anchored_on": ["사용한 anchor 키, 예: dead_parameter:ownerId (없으면 빈 배열)"]
    }
  ]
}"""

FINALIZE_PROMPT = (
    "조사 한도에 도달했습니다. 지금까지 도구로 확인한 근거만으로 "
    "watch_points JSON 객체 하나만 출력하세요. 다른 말은 하지 마세요."
)


def run_watch_point_agent(
    code_files: list[dict[str, Any]],
    provider: Any,
    *,
    max_iterations: int = MAX_ITERATIONS,
    max_tool_calls: int = MAX_TOOL_CALLS,
) -> list[dict[str, Any]] | None:
    """tool 루프로 watch-point를 생성한다. 사용 불가/실패 시 None(→ 폴백)."""
    model = get_solar_chat_model()
    if model is None:
        return None

    try:
        collector: list[str] = []
        tools = build_tools(provider, collector)
        tool_map = {tool.name: tool for tool in tools}
        bound_model = model.bind_tools(tools)
        seed_text = _build_seed(code_files, provider)
    except Exception:
        return None

    messages: list[Any] = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=seed_text),
    ]

    try:
        final_text = _run_loop(bound_model, messages, tool_map, max_iterations, max_tool_calls)
    except Exception:
        return None

    return _finalize(final_text, seed_text, code_files, provider, collector)


def _run_loop(bound_model, messages, tool_map, max_iterations, max_tool_calls) -> str | None:
    calls = 0
    for _ in range(max_iterations):
        response = bound_model.invoke(messages)
        messages.append(response)

        tool_calls = getattr(response, "tool_calls", None)
        if not tool_calls:
            return str(response.content)

        # 한 assistant 메시지의 모든 tool_call에 응답해야 한다(부분 응답 금지).
        for tool_call in tool_calls:
            if calls >= max_tool_calls:
                output = f"[도구 호출 한도 초과: {_tool_call_name(tool_call)}]"
            else:
                output = _run_tool(tool_map, tool_call)
                calls += 1
            messages.append(ToolMessage(content=output, tool_call_id=_tool_call_id(tool_call)))

        if calls >= max_tool_calls:
            messages.append(HumanMessage(content=FINALIZE_PROMPT))
            return str(bound_model.invoke(messages).content)

    # 반복 한도 소진 → 마지막으로 JSON만 요청
    messages.append(HumanMessage(content=FINALIZE_PROMPT))
    return str(bound_model.invoke(messages).content)


def _run_tool(tool_map: dict[str, Any], tool_call: dict[str, Any]) -> str:
    tool = tool_map.get(_tool_call_name(tool_call))
    if tool is None:
        return f"[알 수 없는 도구: {_tool_call_name(tool_call)}]"
    try:
        return str(tool.invoke(_tool_call_args(tool_call)))
    except Exception as error:
        return f"[도구 오류: {error}]"


def _tool_call_name(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "")
    return str(getattr(tool_call, "name", "") or "")


def _tool_call_args(tool_call: Any) -> dict[str, Any]:
    if isinstance(tool_call, dict):
        args = tool_call.get("args") or {}
    else:
        args = getattr(tool_call, "args", {}) or {}
    return args if isinstance(args, dict) else {}


def _tool_call_id(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("id") or "")
    return str(getattr(tool_call, "id", "") or "")


def _build_seed(code_files: list[dict[str, Any]], provider: Any) -> str:
    sections = ["다음 코드 변경을 조사하여 watch-point를 생성하세요.\n"]
    for file_data in code_files:
        anchors = {
            "dead_parameters": file_data.get("dead_parameters", []),
            "shared_mutable_fields": file_data.get("shared_mutable_fields", []),
            "removed_access_control": [
                _mask_secrets(str(line)) for line in file_data.get("removed_access_control", [])
            ],
        }
        body = provider.read_file(file_data["path"])
        body_section = ""
        if body:
            body_section = (
                "변경 후 파일 본문 일부:\n"
                f"{_truncate_seed_text(_mask_secrets(body), MAX_SEED_BODY_CHARS)}\n"
            )
        sections.append(
            f"## {file_data['path']} ({file_data['change_type']})\n"
            f"확정된 구조 관찰(anchors): {anchors}\n"
            f"변경 diff 일부:\n{_mask_secrets(file_data.get('diff_snippet', ''))}\n"
            f"{body_section}"
        )
    sections.append("필요하면 도구로 정의/호출부/주변 코드를 확인한 뒤 watch_points JSON을 출력하세요.")
    return "\n".join(sections)


def _truncate_seed_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


def _finalize(
    final_text: str | None,
    seed_text: str,
    code_files: list[dict[str, Any]],
    provider: Any,
    collector: list[str],
) -> list[dict[str, Any]] | None:
    parsed = _parse_json_object(final_text or "")
    if parsed is None:
        return None  # 파싱 실패 → 폴백
    raw_watch_points = parsed.get("watch_points")
    if not isinstance(raw_watch_points, list):
        return None

    # 인용 코퍼스 = 에이전트가 실제로 본 것(도구 결과) + 변경 본문/diff.
    corpus_parts = [seed_text, *collector]
    corpus = _normalize_ws("\n".join(corpus_parts))

    known_files = {Path(path).name.lower() for path in provider.known_paths()}
    known_files |= {Path(file_data["path"]).name.lower() for file_data in code_files}

    return _validate_watch_points(raw_watch_points, corpus, known_files)
