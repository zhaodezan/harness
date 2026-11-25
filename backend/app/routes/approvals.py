from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class ApprovalDecision(BaseModel):
    approved: bool


@router.post("/{approval_id}")
async def decide_approval(approval_id: str, decision: ApprovalDecision, request: Request):
    pending = request.app.state.pending_approvals.get(approval_id)
    if pending is None or pending.done():
        raise HTTPException(status_code=404, detail="审批请求不存在或已处理")
    pending.set_result(decision.approved)
    return {"status": "approved" if decision.approved else "rejected"}
