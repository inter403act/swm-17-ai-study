from typing import TypedDict, Optional

class PRState(TypedDict):
    pr_data: Optional[dict]
    impact_context: Optional[dict]
    summary_result: Optional[dict]
    risk_result: Optional[dict]
    checklist_result: Optional[dict]
    comment_body: Optional[str]