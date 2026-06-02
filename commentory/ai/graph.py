from langgraph.graph import END, StateGraph

try:
    from agents.pr_analysis import pr_analysis_agent
    from state import PRState
except ModuleNotFoundError:
    from commentory.ai.agents.pr_analysis import pr_analysis_agent
    from commentory.ai.state import PRState


def build_pr_analysis_graph():
    workflow = StateGraph(PRState)
    workflow.add_node("pr_analysis", pr_analysis_agent)
    workflow.set_entry_point("pr_analysis")
    workflow.add_edge("pr_analysis", END)
    return workflow.compile()
