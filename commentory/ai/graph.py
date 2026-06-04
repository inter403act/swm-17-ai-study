from typing import Any, Iterator

from langgraph.graph import END, StateGraph

try:
    from agents.checklist import checklist_node
    from agents.pr_analysis import pr_analysis_agent
    from agents.risk_assessment import risk_assessment_node
    from agents.summary import summary_agent
    from state import PRState
except ModuleNotFoundError:
    from commentory.ai.agents.checklist import checklist_node
    from commentory.ai.agents.pr_analysis import pr_analysis_agent
    from commentory.ai.agents.risk_assessment import risk_assessment_node
    from commentory.ai.agents.summary import summary_agent
    from commentory.ai.state import PRState


STEP_MESSAGES = {
    "pr_analysis": "PR 변경 내용과 영향 범위를 분석 중입니다.",
    "summary": "PR 요약을 생성 중입니다.",
    "risk": "PR 위험도를 평가 중입니다.",
    "checklist": "리뷰 체크리스트를 생성 중입니다.",
    "skip_checklist": "체크리스트 생성을 생략했습니다.",
    "join": "워크플로우 결과를 정리 중입니다.",
}


def route_after_risk(state: PRState) -> str:
    risk_level = (state.get("risk_result") or {}).get("risk_level")
    if risk_level in ("MEDIUM", "HIGH"):
        return "checklist"
    return "skip_checklist"


def summary_node(state: PRState) -> dict[str, Any]:
    next_state = summary_agent(state)
    return {"summary_result": next_state.get("summary_result")}


def skip_checklist_node(state: PRState) -> dict[str, Any]:
    return {"checklist_result": None}


def join_node(state: PRState) -> dict[str, Any]:
    return {}


def build_commentory_graph():
    workflow = StateGraph(PRState)

    workflow.add_node("pr_analysis", pr_analysis_agent)
    workflow.add_node("summary", summary_node)
    workflow.add_node("risk", risk_assessment_node)
    workflow.add_node("checklist", checklist_node)
    workflow.add_node("skip_checklist", skip_checklist_node)
    workflow.add_node("join", join_node)

    workflow.set_entry_point("pr_analysis")

    workflow.add_edge("pr_analysis", "summary")
    workflow.add_edge("pr_analysis", "risk")

    workflow.add_conditional_edges(
        "risk",
        route_after_risk,
        {
            "checklist": "checklist",
            "skip_checklist": "skip_checklist",
        },
    )

    workflow.add_edge(["summary", "checklist"], "join")
    workflow.add_edge(["summary", "skip_checklist"], "join")
    workflow.add_edge("join", END)

    return workflow.compile()


app = build_commentory_graph()


def run_workflow(initial_state: PRState) -> PRState:
    return app.invoke(initial_state)


def stream_workflow_status(initial_state: PRState) -> Iterator[dict[str, Any]]:
    yield {
        "status": "PENDING",
        "current_step": None,
        "message": "Agent workflow 실행을 준비 중입니다.",
    }

    try:
        for event in app.stream(initial_state, stream_mode="updates"):
            for node_name in event.keys():
                yield {
                    "status": "RUNNING",
                    "current_step": node_name,
                    "message": STEP_MESSAGES.get(node_name, f"{node_name} 실행 중입니다."),
                }

        yield {
            "status": "COMPLETED",
            "current_step": "completed",
            "message": "Agent workflow가 완료되었습니다.",
        }
    except Exception as error:
        yield {
            "status": "FAILED",
            "current_step": "failed",
            "message": str(error),
        }
        raise
