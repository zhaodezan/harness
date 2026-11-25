import re


def parse_plan_steps(plan: str, *, max_steps: int = 8) -> list[str]:
    steps = []
    for line in plan.splitlines():
        text = re.sub(r"^\s*(?:(?:\d+|[一二三四五六七八九十]+)[.、)）:]|[-*•])\s*", "", line).strip()
        if text:
            steps.append(text)
    if not steps and plan.strip():
        steps = [plan.strip()]
    if not steps:
        raise ValueError("计划不能为空")
    if len(steps) > max_steps:
        raise ValueError(f"计划步骤不能超过 {max_steps} 步")
    return steps
