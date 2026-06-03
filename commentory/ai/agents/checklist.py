import json
import re

try:
    from agents.llm import invoke_solar
except ModuleNotFoundError:
    from commentory.ai.agents.llm import invoke_solar

try:
    from state import PRState
except ModuleNotFoundError:
    from commentory.ai.state import PRState


SYSTEM_PROMPT = """당신은 코드 리뷰 전문가입니다.
PR 분석 결과와 위험도 평가 결과를 바탕으로 리뷰어가 확인해야 할 체크리스트를 생성하세요.

체크리스트 작성 기준:
- 위험도가 HIGH면 5~8개 항목, MEDIUM이면 3~5개 항목
- 각 항목은 리뷰어가 실제로 확인할 수 있는 구체적인 내용으로 작성
- "~인지 확인" 형태로 작성
- required_review_focus와 risk_signals를 반드시 반영
- missing_test_concerns가 있으면 테스트 관련 항목 반드시 포함

위험도별 체크리스트 가이드:
- HIGH + auth/security: 토큰 검증 로직, 세션 처리, 권한 체크, 예외 처리 누락 여부 확인
- HIGH + database: 마이그레이션 롤백 가능 여부, 데이터 정합성, 트랜잭션 처리 확인
- HIGH + payment: 결제 흐름, 금액 계산 정확성, 예외 처리 확인
- MEDIUM + api: 요청/응답 스펙 변경, 하위 호환성, 에러 응답 처리 확인
- MEDIUM + runtime_behavior: 기존 동작 회귀 가능성, 사이드이펙트 확인

반드시 아래 JSON 형식으로만 반환하세요. 다른 말은 하지 마세요.
{
  "items": ["체크리스트 항목 1", "항목 2", ...]
}"""


def checklist_node(state: PRState) -> dict:
    impact_context = state["impact_context"]
    risk_result = state["risk_result"]

    # 체크리스트 생성에 필요한 정보만 추려서 넘김
    context_for_checklist = {
        "risk_signals": impact_context.get("risk_signals", []),
        "domains": impact_context.get("impact_scope", {}).get("domains", []),
        "affected_apis": impact_context.get("impact_scope", {}).get("affected_apis", []),
        "missing_test_concerns": impact_context.get("test_coverage_signal", {}).get("missing_test_concerns", []),
        "main_changes": impact_context.get("main_changes", []),
        "evidence": impact_context.get("evidence", []),
    }

    user_prompt = f"""아래 정보를 바탕으로 리뷰 체크리스트를 생성하세요.

    ## 위험도 평가 결과
    - risk_level: PR의 위험도 (HIGH/MEDIUM/LOW)
    - risk_reason: 위험도 판단 근거
    - required_review_focus: 리뷰어가 집중해야 할 영역
    {json.dumps(risk_result, ensure_ascii=False, indent=2)}

    ## PR 분석 결과
    - risk_signals: 감지된 위험 신호 (auth, security, database 등)
    - domains: 영향받는 도메인
    - affected_apis: 영향받는 API 경로
    - missing_test_concerns: 테스트가 부족한 부분
    - main_changes: 주요 변경 사항 요약
    - evidence: 변경 근거 snippet
    {json.dumps(context_for_checklist, ensure_ascii=False, indent=2)}

    risk_level, required_review_focus, risk_signals를 중심으로 체크리스트를 생성하세요."""

    response = invoke_solar(SYSTEM_PROMPT, user_prompt)
    return {"checklist_result": _parse_checklist_result(response)}


def _parse_checklist_result(response: str | None) -> dict:
    if response is None:
        return {"items": []}

    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
        return parsed
    except json.JSONDecodeError:
        return {"items": []}