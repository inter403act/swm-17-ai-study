from html import escape
import sys
from pathlib import Path
from typing import Any

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters import (
    fetch_backend_workflow_run,
    fetch_recent_workflow_runs,
)


GRAPH_NODES = [
    "pending",
    "pr_analysis",
    "summary",
    "risk",
    "checklist",
    "skip_checklist",
    "join",
    "completed",
]

STEP_LABELS = {
    "pending": "Ready",
    "pr_analysis": "PR Analysis",
    "summary": "Summary",
    "risk": "Risk",
    "checklist": "Checklist",
    "skip_checklist": "Skip Checklist",
    "join": "Join",
    "completed": "Completed",
    "failed": "Failed",
}


st.set_page_config(page_title="Commentory Workflow", layout="wide")

st.markdown(
    """
    <style>
      .block-container { padding-top: 1.5rem; max-width: 1240px; }
      div[data-testid="stMetric"] {
        border: 1px solid #d7d7d7;
        padding: 0.7rem 0.8rem;
        border-radius: 6px;
        background: #ffffff;
      }
      code { white-space: pre-wrap; }
      .workflow-graph {
        border: 1px solid #dedede;
        border-radius: 6px;
        padding: 1rem;
        background: #ffffff;
      }
      .graph-row {
        display: flex;
        align-items: stretch;
        justify-content: center;
        gap: 0.65rem;
        margin: 0.45rem 0;
      }
      .graph-branch-row {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
        gap: 0.8rem;
        margin: 0.45rem 0;
      }
      .graph-column {
        min-width: 0;
        display: flex;
        flex-direction: column;
        align-items: stretch;
        gap: 0.45rem;
      }
      .workflow-node {
        min-height: 5.2rem;
        border: 1px solid #cfcfcf;
        border-left: 0.35rem solid #a8a8a8;
        border-radius: 6px;
        padding: 0.62rem 0.7rem;
        background: #f8f8f8;
      }
      .workflow-node.done {
        border-left-color: #217a4b;
        background: #f2faf5;
      }
      .workflow-node.running {
        border-left-color: #c97a16;
        background: #fff7eb;
      }
      .workflow-node.failed {
        border-left-color: #c93535;
        background: #fff1f1;
      }
      .workflow-node.skipped {
        border-left-color: #9b9b9b;
        background: #f4f4f4;
        color: #777;
      }
      .workflow-node-title {
        font-weight: 650;
        color: #222;
        overflow-wrap: anywhere;
      }
      .workflow-node-status {
        display: inline-block;
        margin-top: 0.25rem;
        font-size: 0.76rem;
        font-weight: 700;
        color: #4b4b4b;
        text-transform: uppercase;
      }
      .workflow-node-message {
        margin-top: 0.35rem;
        font-size: 0.88rem;
        color: #555;
        overflow-wrap: anywhere;
      }
      .graph-edge {
        min-width: 2rem;
        display: flex;
        align-items: center;
        justify-content: center;
        color: #696969;
        font-weight: 700;
      }
      .graph-edge-down {
        text-align: center;
        color: #696969;
        font-size: 1.15rem;
        line-height: 1.1;
        margin: 0.25rem 0;
      }
      .graph-edge-label {
        color: #666;
        font-size: 0.78rem;
        font-weight: 650;
        text-align: center;
      }
      .risk-badge {
        display: inline-block;
        padding: 0.18rem 0.5rem;
        border-radius: 4px;
        color: #ffffff;
        font-weight: 700;
        background: #4c6f91;
      }
      .risk-badge.medium { background: #b56a14; }
      .risk-badge.high { background: #b63131; }
      .risk-badge.low { background: #217a4b; }
    </style>
    """,
    unsafe_allow_html=True,
)


def init_session() -> None:
    defaults = {
        "pr_ref": None,
        "repository_ref": None,
        "pull_requests": [],
        "workflow_input": None,
        "workflow_result": None,
        "events": [],
        "is_running": False,
        "latest_backend_run_id": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def normalize_step(event: dict[str, Any]) -> str:
    status = event.get("status")
    current_step = event.get("current_step")
    if status == "PENDING":
        return "pending"
    if current_step is None:
        return "pending"
    return str(current_step)


def workflow_node_states(events: list[dict[str, Any]]) -> dict[str, str]:
    if not events:
        return {step: "waiting" for step in GRAPH_NODES}

    latest_nodes = events[-1].get("nodes") or {}
    return {
        step: latest_nodes.get(step, "waiting")
        for step in GRAPH_NODES
    }


def workflow_progress(events: list[dict[str, Any]]) -> float:
    states = workflow_node_states(events)
    done_count = sum(1 for state in states.values() if state == "done")
    active_count = sum(1 for state in states.values() if state != "skipped")
    return done_count / active_count if active_count else 0.0


def render_workflow_graph(events: list[dict[str, Any]]) -> None:
    if not events:
        st.info("Waiting for a workflow run.")
        st.progress(0.0)
        states = workflow_node_states(events)
        render_graph_blocks(states, {})
        return

    latest = events[-1]
    st.progress(workflow_progress(events))
    st.caption(latest.get("message") or "")

    states = workflow_node_states(events)
    messages = {normalize_step(event): event.get("message", "") for event in events}
    if "skip_checklist" in messages:
        messages["skip_checklist"] = messages["skip_checklist"]

    render_graph_blocks(states, messages)


def render_graph_blocks(states: dict[str, str], messages: dict[str, str]) -> None:
    st.markdown(
        f"""
        <div class="workflow-graph">
          <div class="graph-row">
            {workflow_node_html("pending", states["pending"], messages.get("pending", ""))}
            <div class="graph-edge">-&gt;</div>
            {workflow_node_html("pr_analysis", states["pr_analysis"], messages.get("pr_analysis", ""))}
          </div>
          <div class="graph-edge-down">v<div class="graph-edge-label">fan out</div></div>
          <div class="graph-branch-row">
            <div class="graph-column">
              {workflow_node_html("summary", states["summary"], messages.get("summary", ""))}
            </div>
            <div class="graph-column">
              {workflow_node_html("risk", states["risk"], messages.get("risk", ""))}
              <div class="graph-edge-down">v<div class="graph-edge-label">risk route</div></div>
              <div class="graph-branch-row">
                {workflow_node_html("checklist", states["checklist"], messages.get("checklist", ""))}
                {workflow_node_html("skip_checklist", states["skip_checklist"], messages.get("skip_checklist", ""))}
              </div>
            </div>
          </div>
          <div class="graph-edge-down">v<div class="graph-edge-label">join</div></div>
          <div class="graph-row">
            {workflow_node_html("join", states["join"], messages.get("join", ""))}
            <div class="graph-edge">-&gt;</div>
            {workflow_node_html("completed", states["completed"], messages.get("completed", ""))}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def workflow_node_html(step: str, state: str, message: str) -> str:
    label = escape(STEP_LABELS.get(step, step))
    escaped_message = escape(message)
    status_text = {
        "waiting": "Waiting",
        "running": "Running",
        "done": "Done",
        "failed": "Failed",
        "skipped": "Skipped",
    }[state]
    return (
        f'<div class="workflow-node {state}">'
        f'<div class="workflow-node-title">{label}</div>'
        f'<div class="workflow-node-status">{status_text}</div>'
        f'<div class="workflow-node-message">{escaped_message}</div>'
        "</div>"
    )


def render_workflow_result(result: dict[str, Any]) -> None:
    summary_markdown = (result.get("summary_result") or {}).get("markdown") or ""
    risk_result = result.get("risk_result") or {}
    checklist_items = (result.get("checklist_result") or {}).get("items") or []

    summary_tab, risk_tab, checklist_tab, raw_tab = st.tabs(["Summary", "Risk", "Checklist", "Raw"])
    with summary_tab:
        st.markdown(summary_markdown or "_No summary result yet._")
    with risk_tab:
        risk_level = str(risk_result.get("risk_level") or "UNKNOWN")
        badge_class = risk_level.lower()
        st.markdown(
            f'<span class="risk-badge {badge_class}">{risk_level}</span>',
            unsafe_allow_html=True,
        )
        risk_reasons = risk_result.get("risk_reason") or []
        if risk_reasons:
            st.markdown("#### Reasons")
            for reason in risk_reasons:
                st.write(f"- {reason}")
        else:
            st.write("No risk reasons available.")
    with checklist_tab:
        if checklist_items:
            for index, item in enumerate(checklist_items):
                st.checkbox(item, value=False, key=f"checklist-{index}-{item}")
        else:
            st.write("No checklist was generated.")
    with raw_tab:
        st.json(result)


def sync_latest_backend_workflow_run() -> None:
    try:
        recent_runs = fetch_recent_workflow_runs()
    except Exception:
        return
    if not recent_runs:
        return

    latest_run = recent_runs[0]
    repository = latest_run.get("repository")
    pull_number = latest_run.get("pull_number")
    if not repository or pull_number is None:
        return

    try:
        run = fetch_backend_workflow_run(repository, int(pull_number))
    except Exception:
        return
    if run.get("status") == "NOT_FOUND":
        return

    st.session_state.latest_backend_run_id = run.get("run_id")
    st.session_state.events = run.get("events") or []
    st.session_state.workflow_result = run.get("result")


@st.fragment(run_every="1s")
def render_live_workflow_sections() -> None:
    sync_latest_backend_workflow_run()

    st.subheader("Workflow")
    render_workflow_graph(st.session_state.events)

    st.subheader("Result")
    if st.session_state.workflow_result:
        render_workflow_result(st.session_state.workflow_result)
    else:
        st.write("workflow result is not available yet.")


init_session()

st.title("Commentory Workflow Console")

render_live_workflow_sections()
