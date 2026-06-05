from html import escape
import sys
from pathlib import Path
from typing import Any, Callable, Iterator

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters import (
    DEFAULT_TEST_REPOSITORY_URL,
    PullRequestRef,
    build_comment_body,
    create_risk_test_pull_request,
    fetch_open_pull_requests,
    fetch_workflow_input,
    parse_pr_url,
    parse_repository_url,
    post_comment,
    run_agent_workflow_for_ui,
)


WorkflowRunner = Callable[[dict[str, Any]], Iterator[dict[str, Any]]]
CommentBuilder = Callable[[str, int, dict[str, Any]], str]

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
      .workflow-history {
        font-size: 0.9rem;
        color: #555;
        padding: 0.3rem 0;
        border-bottom: 1px solid #f1f1f1;
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
        "comment_body": "",
        "events": [],
        "is_running": False,
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
    states = {step: "waiting" for step in GRAPH_NODES}
    if not events:
        return states

    for index, event in enumerate(events):
        step = normalize_step(event)
        event_type = event.get("event_type")
        status = event.get("status")

        if status == "PENDING":
            states["pending"] = "running"
            continue

        if event_type == "node_started":
            states["pending"] = "done"
            if step in states:
                states[step] = "running"
            continue

        if event_type == "node_finished":
            if step in states:
                states[step] = "done"
            if step == "checklist":
                states["skip_checklist"] = "skipped"
            if step == "skip_checklist":
                states["checklist"] = "skipped"
            continue

        if status == "FAILED":
            states["pending"] = "done"
            if step in states:
                states[step] = "failed"
            else:
                states["completed"] = "failed"
            continue

        if status == "COMPLETED":
            states["pending"] = "done"
            for completed_step in ("pr_analysis", "summary", "risk", "join"):
                if states[completed_step] == "running":
                    states[completed_step] = "done"
            if states["checklist"] == "running":
                states["checklist"] = "done"
                states["skip_checklist"] = "skipped"
            if states["skip_checklist"] == "running":
                states["skip_checklist"] = "done"
                states["checklist"] = "skipped"
            states["completed"] = "done"
            continue

        if event_type is None and step in states:
            states[step] = "running" if index == len(events) - 1 else "done"

    return states


def workflow_progress(events: list[dict[str, Any]]) -> float:
    states = workflow_node_states(events)
    done_count = sum(1 for state in states.values() if state == "done")
    active_count = sum(1 for state in states.values() if state != "skipped")
    return done_count / active_count if active_count else 0.0


def render_workflow_graph(events: list[dict[str, Any]]) -> None:
    if not events:
        st.info("workflow 실행 전입니다.")
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
            <div class="graph-edge">→</div>
            {workflow_node_html("pr_analysis", states["pr_analysis"], messages.get("pr_analysis", ""))}
          </div>
          <div class="graph-edge-down">↓<div class="graph-edge-label">fan out</div></div>
          <div class="graph-branch-row">
            <div class="graph-column">
              {workflow_node_html("summary", states["summary"], messages.get("summary", ""))}
            </div>
            <div class="graph-column">
              {workflow_node_html("risk", states["risk"], messages.get("risk", ""))}
              <div class="graph-edge-down">↓<div class="graph-edge-label">risk route</div></div>
              <div class="graph-branch-row">
                {workflow_node_html("checklist", states["checklist"], messages.get("checklist", ""))}
                {workflow_node_html("skip_checklist", states["skip_checklist"], messages.get("skip_checklist", ""))}
              </div>
            </div>
          </div>
          <div class="graph-edge-down">↓<div class="graph-edge-label">join</div></div>
          <div class="graph-row">
            {workflow_node_html("join", states["join"], messages.get("join", ""))}
            <div class="graph-edge">→</div>
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


def render_event_history(events: list[dict[str, Any]]) -> None:
    if not events:
        return

    with st.expander("Event History", expanded=False):
        for event in events:
            status = escape(str(event.get("status", "UNKNOWN")))
            step = escape(str(event.get("current_step") or "-"))
            message = escape(str(event.get("message") or ""))
            st.markdown(
                f'<div class="workflow-history"><code>{status}</code> · <code>{step}</code> · {message}</div>',
                unsafe_allow_html=True,
            )


def render_workflow_result(result: dict[str, Any]) -> None:
    summary_markdown = (result.get("summary_result") or {}).get("markdown") or ""
    risk_result = result.get("risk_result") or {}
    checklist_items = (result.get("checklist_result") or {}).get("items") or []

    summary_tab, risk_tab, checklist_tab, raw_tab = st.tabs(["Summary", "Risk", "Checklist", "Raw"])
    with summary_tab:
        st.markdown(summary_markdown or "_요약 결과가 없습니다._")
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
            st.write("위험도 사유가 없습니다.")
    with checklist_tab:
        if checklist_items:
            for index, item in enumerate(checklist_items):
                st.checkbox(item, value=False, key=f"checklist-{index}-{item}")
        else:
            st.write("체크리스트가 생성되지 않았습니다.")
    with raw_tab:
        st.json(result)


def render_context_summary(workflow_input: dict[str, Any]) -> None:
    repo_tree = workflow_input.get("repo_tree") or []
    repository_file_contents = workflow_input.get("repository_file_contents") or []
    initial_pr_data = (workflow_input.get("initial_state") or {}).get("pr_data") or {}

    with st.expander("Repository Context", expanded=False):
        metric_cols = st.columns(3)
        metric_cols[0].metric("Repo tree files", len(repo_tree))
        metric_cols[1].metric("Context files", len(repository_file_contents))
        metric_cols[2].metric("Workflow tree files", len(initial_pr_data.get("repo_tree") or []))

        if repository_file_contents:
            st.write("Loaded context")
            for file_data in repository_file_contents:
                path = file_data.get("path", "-")
                content = file_data.get("content") or ""
                st.write(f"`{path}` · {len(content):,} chars")
        else:
            st.write("관련 repository file content가 없습니다.")


def render_pr_overview(pr_ref: PullRequestRef, workflow_input: dict[str, Any]) -> None:
    pull_request = workflow_input["pull_request"]
    changed_files = workflow_input["changed_files"]
    repo_tree = workflow_input.get("repo_tree") or []
    repository_file_contents = workflow_input.get("repository_file_contents") or []
    metric_cols = st.columns(6)
    metric_cols[0].metric("Repository", pr_ref.repository)
    metric_cols[1].metric("PR", f"#{pr_ref.pull_number}")
    metric_cols[2].metric("Changed files", len(changed_files))
    metric_cols[3].metric("State", pull_request.get("state", "-"))
    metric_cols[4].metric("Repo tree", len(repo_tree))
    metric_cols[5].metric("Context files", len(repository_file_contents))

    st.subheader(pull_request.get("title") or "Untitled PR")
    with st.expander("Changed Files", expanded=False):
        for changed_file in changed_files:
            additions = changed_file.get("additions", 0)
            deletions = changed_file.get("deletions", 0)
            status = changed_file.get("status", "-")
            filename = changed_file.get("filename", "-")
            st.write(f"`{status}` `{filename}` · +{additions} / -{deletions}")
    render_context_summary(workflow_input)


def render_open_pull_requests(pull_requests: list[dict[str, Any]]) -> None:
    if not pull_requests:
        return

    with st.expander("Open Pull Requests", expanded=False):
        st.dataframe(
            [
                {
                    "PR": f"#{pull_request.get('number')}",
                    "Title": pull_request.get("title"),
                    "Author": pull_request.get("user"),
                    "Head": pull_request.get("head"),
                    "Base": pull_request.get("base"),
                    "URL": pull_request.get("html_url"),
                }
                for pull_request in pull_requests
            ],
            hide_index=True,
            width="stretch",
        )


def reset_workflow_state() -> None:
    st.session_state.workflow_result = None
    st.session_state.comment_body = ""
    st.session_state.events = []


def reset_repository_state() -> None:
    st.session_state.repository_ref = None
    st.session_state.pull_requests = []
    st.session_state.pr_ref = None
    st.session_state.workflow_input = None
    reset_workflow_state()


def run_workflow(runner: WorkflowRunner, comment_builder: CommentBuilder) -> None:
    workflow_input = st.session_state.workflow_input
    pr_ref = st.session_state.pr_ref
    if workflow_input is None or pr_ref is None:
        st.error("workflow input이 준비되지 않았습니다.")
        return

    reset_workflow_state()
    st.session_state.is_running = True
    initial_state = workflow_input["initial_state"]
    status_area = st.empty()

    try:
        for update in runner(initial_state):
            event = update["event"]
            st.session_state.events.append(event)
            with status_area.container():
                render_workflow_graph(st.session_state.events)

            if update["type"] == "result":
                result = update["result"]
                st.session_state.workflow_result = result
                st.session_state.comment_body = comment_builder(
                    pr_ref.repository,
                    pr_ref.pull_number,
                    result,
                )
    except Exception as exc:
        failed_event = {
            "status": "FAILED",
            "current_step": "failed",
            "message": str(exc),
        }
        st.session_state.events.append(failed_event)
        st.error(str(exc))
    finally:
        st.session_state.is_running = False
        status_area.empty()


def create_test_pr_and_run(repository_url: str, risk_level: str) -> None:
    try:
        repository_ref = parse_repository_url(repository_url)
        pr_ref = create_risk_test_pull_request(repository_ref, risk_level)
        workflow_input = fetch_workflow_input(pr_ref)
    except Exception as exc:
        st.error(str(exc))
        return

    st.session_state.repository_ref = repository_ref
    st.session_state.pr_ref = pr_ref
    st.session_state.workflow_input = workflow_input
    reset_workflow_state()
    run_workflow(run_agent_workflow_for_ui, build_comment_body)


init_session()

st.title("Commentory Workflow Console")

with st.sidebar:
    st.header("PR")
    repository_url = st.text_input(
        "GitHub Repository URL",
        value=DEFAULT_TEST_REPOSITORY_URL,
        placeholder="https://github.com/owner/repo",
    )
    st.caption("Create a real fixture PR and immediately run the workflow.")
    risk_cols = st.columns(3)
    high_clicked = risk_cols[0].button("HIGH", width="stretch", disabled=st.session_state.is_running)
    medium_clicked = risk_cols[1].button("MEDIUM", width="stretch", disabled=st.session_state.is_running)
    low_clicked = risk_cols[2].button("LOW", width="stretch", disabled=st.session_state.is_running)

    st.divider()
    load_prs_clicked = st.button(
        "Load Open PRs",
        width="stretch",
        disabled=st.session_state.is_running,
    )
    pull_requests = st.session_state.pull_requests
    selected_pr_number = None
    if pull_requests:
        selected_label = st.selectbox(
            "Open PR",
            [
                f"#{pull_request['number']} · {pull_request['title']}"
                for pull_request in pull_requests
            ],
        )
        selected_pr_number = int(selected_label.split(" · ", 1)[0].removeprefix("#"))
    fetch_selected_clicked = st.button(
        "Fetch Selected PR",
        width="stretch",
        disabled=selected_pr_number is None or st.session_state.is_running,
    )
    st.divider()
    pr_url = st.text_input(
        "GitHub PR URL",
        placeholder="https://github.com/owner/repo/pull/1",
    )
    fetch_clicked = st.button(
        "Fetch PR URL",
        width="stretch",
        disabled=st.session_state.is_running,
    )
    run_disabled = st.session_state.workflow_input is None or st.session_state.is_running
    post_disabled = (
        not st.session_state.comment_body
        or st.session_state.pr_ref is None
        or st.session_state.is_running
    )

    run_clicked = st.button(
        "Run Workflow",
        disabled=run_disabled,
        width="stretch",
    )
    post_clicked = st.button(
        "Post Comment",
        disabled=post_disabled,
        width="stretch",
    )

if high_clicked:
    create_test_pr_and_run(repository_url, "HIGH")

if medium_clicked:
    create_test_pr_and_run(repository_url, "MEDIUM")

if low_clicked:
    create_test_pr_and_run(repository_url, "LOW")

if fetch_clicked:
    try:
        pr_ref = parse_pr_url(pr_url)
        workflow_input = fetch_workflow_input(pr_ref)
    except Exception as exc:
        st.error(str(exc))
    else:
        st.session_state.pr_ref = pr_ref
        st.session_state.workflow_input = workflow_input
        reset_workflow_state()
        st.rerun()

if load_prs_clicked:
    try:
        repository_ref = parse_repository_url(repository_url)
        pull_requests = fetch_open_pull_requests(repository_ref)
    except Exception as exc:
        st.error(str(exc))
    else:
        st.session_state.repository_ref = repository_ref
        st.session_state.pull_requests = pull_requests
        st.session_state.pr_ref = None
        st.session_state.workflow_input = None
        reset_workflow_state()
        if pull_requests:
            st.rerun()
        else:
            st.info(f"{repository_ref.repository}에 open PR이 없습니다.")

if fetch_selected_clicked:
    repository_ref = st.session_state.repository_ref
    if repository_ref is None:
        st.error("먼저 Open PR 목록을 불러와야 합니다.")
    else:
        try:
            pr_ref = PullRequestRef(
                owner=repository_ref.owner,
                repo=repository_ref.repo,
                pull_number=selected_pr_number,
            )
            workflow_input = fetch_workflow_input(pr_ref)
        except Exception as exc:
            st.error(str(exc))
        else:
            st.session_state.pr_ref = pr_ref
            st.session_state.workflow_input = workflow_input
            reset_workflow_state()
            st.rerun()

if run_clicked:
    run_workflow(run_agent_workflow_for_ui, build_comment_body)

if post_clicked:
    try:
        created_comment = post_comment(st.session_state.pr_ref, st.session_state.comment_body)
    except Exception as exc:
        st.error(str(exc))
    else:
        st.success(created_comment.get("html_url", "Comment posted."))

pr_ref = st.session_state.pr_ref
workflow_input = st.session_state.workflow_input

if workflow_input and pr_ref:
    render_pr_overview(pr_ref, workflow_input)
elif st.session_state.pull_requests:
    render_open_pull_requests(st.session_state.pull_requests)

st.subheader("Workflow")
render_workflow_graph(st.session_state.events)
render_event_history(st.session_state.events)

result_col, comment_col = st.columns([0.52, 0.48], gap="large")

with result_col:
    st.subheader("Result")
    if st.session_state.workflow_result:
        render_workflow_result(st.session_state.workflow_result)
    else:
        st.write("workflow 결과가 없습니다.")

with comment_col:
    st.subheader("Comment Preview")
    if st.session_state.comment_body:
        st.markdown(st.session_state.comment_body)
    else:
        st.write("생성된 comment body가 없습니다.")
