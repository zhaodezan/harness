import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_installer():
    spec = importlib.util.spec_from_file_location("harness_installer_idempotent", ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reinstall_does_not_delete_existing_frontend_modules():
    module = load_installer()
    commands = module.install_commands(
        python_executable="python-test", npm_executable="npm-test", frontend_modules_exists=True
    )
    assert commands[1] == ["npm-test", "install", "--prefix", "frontend"]


def test_first_frontend_install_uses_package_lock_clean_install():
    module = load_installer()
    commands = module.install_commands(
        python_executable="python-test", npm_executable="npm-test", frontend_modules_exists=False
    )
    assert commands[1] == ["npm-test", "ci", "--prefix", "frontend"]
