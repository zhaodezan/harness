import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_installer():
    spec = importlib.util.spec_from_file_location("harness_installer_versions", ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_installer_rejects_old_node_major_version():
    module = load_installer()
    assert module.validate_prerequisites((3, 13), "npm", node_version=(18, 19, 0)) == (
        False,
        "需要 Node.js 20 或更新版本。当前检测到 18.19.0。",
    )
