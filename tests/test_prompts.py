import pytest

from backend.app.prompts import render_prompt


def test_render_prompt_replaces_named_variables_without_python_formatting():
    assert render_prompt("Hello {{ user_name }} — {{project}}", {"user_name": "Lin", "project": "Harness"}) == "Hello Lin — Harness"


def test_render_prompt_rejects_undefined_variables():
    with pytest.raises(ValueError, match="missing"):
        render_prompt("Hello {{missing}}", {})


def test_render_prompt_leaves_non_placeholder_braces_untouched():
    assert render_prompt("JSON: {\"ok\":true}; {{name}}", {"name": "A"}) == 'JSON: {"ok":true}; A'
