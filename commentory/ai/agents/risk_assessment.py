import json

try:
    from agents.llm import invoke_solar
except ModuleNotFoundError:
    from commentory.ai.agents.llm import invoke_solar

try:
    from state import PRState
except ModuleNotFoundError:
    from commentory.ai.state import PRState


SYSTEM_PROMPT = """당신은 코드 리뷰 전문가입니다.
PR 분석 결과를 보고 위험도를 평가하세요.

위험도 판단 기준:
- HIGH: 아래 중 하나라도 해당되면 HIGH
  - 인증/보안 관련 로직 변경 (auth, jwt, token, secret, password, permission)
  - 결제 관련 변경 (payment, billing, checkout)
  - DB 스키마/마이그레이션 변경
  - 핵심 비즈니스 로직 변경

- MEDIUM: 아래 중 하나라도 해당되면 MEDIUM
  - API 스펙 변경 (endpoint 추가/수정/삭제)
  - 서비스 레이어 수정
  - 테스트 없는 로직 변경

- LOW: 아래에만 해당되면 LOW
  - 문서, 스타일 변경
  - 설정 파일 변경
  - 테스트만 변경

반드시 아래 JSON 형식으로만 반환하세요. 다른 말은 하지 마세요.
{
  "risk_level": "HIGH | MEDIUM | LOW 중 하나",
  "risk_reason": ["판단 근거 1", "판단 근거 2"],
  "routing_target": "high_risk_checklist | medium_risk_checklist | none 중 하나",
  "required_review_focus": ["리뷰어가 집중해야 할 항목 1", "항목 2"],
  "uncertainty": "high | medium | low 중 하나"
}"""


def risk_assessment_node(state: PRState) -> dict:
    impact_context = state["impact_context"]

    user_prompt = f"""아래 PR 분석 결과를 보고 위험도를 평가하세요.

PR 분석 결과:
{json.dumps(impact_context, ensure_ascii=False, indent=2)}"""

    response = invoke_solar(SYSTEM_PROMPT, user_prompt)

    risk_result = _parse_risk_result(response)

    return {"risk_result": risk_result}


def _parse_risk_result(response: str | None) -> dict:
    if response is None:
        return {
            "risk_level": "MEDIUM",
            "risk_reason": ["분석 결과를 가져오지 못했습니다."],
            "routing_target": "medium_risk_checklist",
            "required_review_focus": [],
            "uncertainty": "high",
        }

    cleaned = response.strip()
    if cleaned.startswith("```"):
        import re
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
        return parsed
    except json.JSONDecodeError:
        return {
            "risk_level": "MEDIUM",
            "risk_reason": ["응답 파싱에 실패했습니다."],
            "routing_target": "medium_risk_checklist",
            "required_review_focus": [],
            "uncertainty": "high",
        }