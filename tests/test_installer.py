import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_installer_prerequisite_check_explains_missing_node(monkeypatch):
    spec = importlib.util.spec_from_file_location("harness_installer", ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except FileNotFoundError:
        assert False, "install.py must be available"

    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    assert module.validate_prerequisites(python_version=(3, 13), npm_path=None) == (
        False,
        "未找到 Node.js/npm。请安装 Node.js 20 或更新版本，然后重新运行安装器。",
    )


def test_install_commands_are_explicit_and_use_locked_frontend_dependencies():
    spec = importlib.util.spec_from_file_location("harness_installer", ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    commands = module.install_commands(
        python_executable="python-test", npm_executable="npm-test", frontend_modules_exists=False
    )

    assert commands == [
        ["python-test", "-m", "pip", "install", "-e", "."],
        ["npm-test", "ci", "--prefix", "frontend"],
        ["npm-test", "run", "build", "--prefix", "frontend"],
    ]
