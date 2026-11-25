import uuid

MAX_GENERATED_CODE_CHARS = 64_000
MAX_CODE_ARGUMENT_BYTES = 64 * 1024


def sandbox_code_tool(existing_tools: list[dict]) -> dict:
    existing_names = {tool["name"] for tool in existing_tools}
    name = "sandbox:run_python"
    while name in existing_names:
        name = f"sandbox:run_python:{uuid.uuid4().hex[:8]}"
    return {
        "name": name,
        "description": "Run user-reviewed Python code in a temporary CubeSandbox. Never use for secrets or host filesystem access.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "maxLength": MAX_GENERATED_CODE_CHARS},
                "arguments": {"type": "object"},
            },
            "required": ["code", "arguments"],
            "additionalProperties": False,
        },
        "risk": "side_effect",
        "plugin_id": "internal:cube-sandbox-code",
    }
