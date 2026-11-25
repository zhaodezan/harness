from pathlib import Path
import os


def get_data_dir() -> Path:
    configured = os.environ.get("AGENT_HARNESS_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".agent-harness"
