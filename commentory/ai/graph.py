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

WORKFLOW_NODES = [
    "pending",
    "pr_analysis",
    "summary",
    "risk",
    "checklist",
    "skip_checklist",
    "join",
    "completed",
]


def _initial_node_statuses() -> dict[str, str]:
    return {node: "waiting" for node in WORKFLOW_NODES}


def _status_event(
    status: str,
    current_step: str | None,
    message: str,
    node_statuses: dict[str, str],
    result: PRState | None = None,
) -> dict[str, Any]:
    event = {
        "status": status,
        "current_step": current_step,
        "message": message,
        "nodes": dict(node_statuses),
    }
    if result is not None:
        event["result"] = result
    return event


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
    workflow_result = None
    for event in stream_workflow_status(initial_state):
        workflow_result = event.get("result") or workflow_result

    if workflow_result is None:
        raise RuntimeError("Agent workflow completed without a result.")
    return workflow_result


def stream_workflow_status(initial_state: PRState) -> Iterator[dict[str, Any]]:
    node_statuses = _initial_node_statuses()
    node_statuses["pending"] = "running"
    workflow_result: PRState = dict(initial_state)

    yield _status_event(
        "PENDING",
        None,
        "Agent workflow 실행을 준비 중입니다.",
        node_statuses,
    )

    try:
        for event in app.stream(initial_state, stream_mode="updates"):
            node_statuses["pending"] = "done"
            for node_name in event.keys():
                node_update = event.get(node_name)
                if isinstance(node_update, dict):
                    workflow_result.update(node_update)

                if node_name in node_statuses:
                    node_statuses[node_name] = "done"
                if node_name == "checklist":
                    node_statuses["skip_checklist"] = "skipped"
                if node_name == "skip_checklist":
                    node_statuses["checklist"] = "skipped"

                yield _status_event(
                    "RUNNING",
                    node_name,
                    STEP_MESSAGES.get(node_name, f"{node_name} 실행 중입니다."),
                    node_statuses,
                )

        node_statuses["pending"] = "done"
        node_statuses["completed"] = "done"
        yield _status_event(
            status="COMPLETED",
            current_step="completed",
            message="Agent workflow가 완료되었습니다.",
            node_statuses=node_statuses,
            result=workflow_result,
        )
    except Exception as error:
        node_statuses["pending"] = "done"
        node_statuses["completed"] = "failed"
        yield _status_event("FAILED", "failed", str(error), node_statuses)
        raise
