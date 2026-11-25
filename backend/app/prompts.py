import re


_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def render_prompt(template: str, variables: dict[str, str]) -> str:
    missing = sorted({name for name in _PLACEHOLDER.findall(template) if name not in variables})
    if missing:
        raise ValueError(f"未设置 Prompt 变量: {', '.join(missing)}")
    return _PLACEHOLDER.sub(lambda match: variables[match.group(1)], template)
