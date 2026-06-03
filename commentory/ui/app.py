from typing import Any

import streamlit as st

from adapters import (
    IntegrationDependencyError,
    build_comment_body,
    fetch_workflow_input,
    parse_pr_url,
    post_comment,
    run_agent_workflow_for_ui,
)


st.set_page_config(page_title="Commentory Workflow", layout="wide")

st.markdown(
    """
    <style>
      .block-container { padding-top: 2rem; max-width: 1180px; }
      div[data-testid="stMetric"] { border: 1px solid #d8d2c4; padding: 0.7rem 0.8rem; border-radius: 6px; }
      code { white-space: pre-wrap; }
    </style>
    """,
    unsafe_allow_html=True,
)


def init_session() -> None:
    defaults = {
        "pr_ref": None,
        "workflow_input": None,
        "workflow_result": None,
        "comment_body": "",
        "events": [],
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def render_status_events(events: list[dict[str, Any]]) -> None:
    if not events:
        st.info("아직 실행된 workflow가 없습니다.")
        return

    for event in events:
        status = event.get("status", "UNKNOWN")
        step = event.get("current_step") or "-"
        message = event.get("message") or ""
        st.write(f"`{status}` · `{step}` · {message}")


def render_workflow_result(result: dict[str, Any]) -> None:
    summary_markdown = (result.get("summary_result") or {}).get("markdown") or ""
    risk_result = result.get("risk_result") or {}
    checklist_items = (result.get("checklist_result") or {}).get("items") or []

    summary_tab, risk_tab, checklist_tab, raw_tab = st.tabs(["Summary", "Risk", "Checklist", "Raw"])
    with summary_tab:
        st.markdown(summary_markdown or "_요약 결과가 없습니다._")
    with risk_tab:
        st.json(risk_result)
    with checklist_tab:
        if checklist_items:
            for item in checklist_items:
                st.checkbox(item, value=False)
        else:
            st.write("체크리스트가 생성되지 않았습니다.")
    with raw_tab:
        st.json(result)


init_session()

st.title("Commentory Workflow Console")

with st.sidebar:
    st.header("PR")
    pr_url = st.text_input(
        "GitHub PR URL",
        placeholder="https://github.com/owner/repo/pull/1",
    )
    fetch_clicked = st.button("Fetch PR", use_container_width=True)
    run_clicked = st.button(
        "Run Workflow",
        disabled=st.session_state.workflow_input is None,
        use_container_width=True,
    )
    post_clicked = st.button(
        "Post Comment",
        disabled=not st.session_state.comment_body or st.session_state.pr_ref is None,
        use_container_width=True,
    )

if fetch_clicked:
    try:
        pr_ref = parse_pr_url(pr_url)
        workflow_input = fetch_workflow_input(pr_ref)
    except (ValueError, IntegrationDependencyError, RuntimeError) as exc:
        st.error(str(exc))
    else:
        st.session_state.pr_ref = pr_ref
        st.session_state.workflow_input = workflow_input
        st.session_state.workflow_result = None
        st.session_state.comment_body = ""
        st.session_state.events = []
        st.toast("PR 데이터를 불러왔습니다.")

if run_clicked and st.session_state.workflow_input:
    st.session_state.events = []
    initial_state = st.session_state.workflow_input["initial_state"]
    status_area = st.empty()

    try:
        for update in run_agent_workflow_for_ui(initial_state):
            event = update["event"]
            st.session_state.events.append(event)
            with status_area.container():
                render_status_events(st.session_state.events)

            if update["type"] == "result":
                result = update["result"]
                pr_ref = st.session_state.pr_ref
                st.session_state.workflow_result = result
                st.session_state.comment_body = build_comment_body(
                    pr_ref.repository,
                    pr_ref.pull_number,
                    result,
                )
    except (IntegrationDependencyError, RuntimeError, ValueError) as exc:
        st.error(str(exc))

if post_clicked:
    try:
        created_comment = post_comment(st.session_state.pr_ref, st.session_state.comment_body)
    except (IntegrationDependencyError, RuntimeError) as exc:
        st.error(str(exc))
    else:
        st.success(created_comment.get("html_url", "Comment posted."))

pr_ref = st.session_state.pr_ref
workflow_input = st.session_state.workflow_input

if workflow_input and pr_ref:
    pull_request = workflow_input["pull_request"]
    changed_files = workflow_input["changed_files"]
    metric_cols = st.columns(4)
    metric_cols[0].metric("Repository", pr_ref.repository)
    metric_cols[1].metric("PR", f"#{pr_ref.pull_number}")
    metric_cols[2].metric("Changed files", len(changed_files))
    metric_cols[3].metric("State", pull_request.get("state", "-"))

    st.subheader(pull_request.get("title") or "Untitled PR")

left_col, right_col = st.columns([0.52, 0.48], gap="large")

with left_col:
    st.subheader("Workflow")
    render_status_events(st.session_state.events)

    if st.session_state.workflow_result:
        st.subheader("Result")
        render_workflow_result(st.session_state.workflow_result)

with right_col:
    st.subheader("Comment Preview")
    if st.session_state.comment_body:
        st.markdown(st.session_state.comment_body)
    else:
        st.write("생성된 comment body가 없습니다.")
