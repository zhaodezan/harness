from typing import Literal

from backend.app.tools import chat_tool_requires_approval


ApprovalMode = Literal["allow_all", "user_approval"]
DEFAULT_APPROVAL_MODE: ApprovalMode = "allow_all"


def should_wait_for_approval(tool: dict, mode: ApprovalMode) -> bool:
    return mode == "user_approval" and chat_tool_requires_approval(tool)
