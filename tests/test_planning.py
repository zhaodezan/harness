import pytest

from backend.app.runtime.planning import parse_plan_steps


def test_parse_plan_steps_handles_numbered_and_bulleted_lists():
    assert parse_plan_steps("1. Inspect\n2) Change code\n- Run tests") == ["Inspect", "Change code", "Run tests"]


def test_plan_step_count_is_bounded():
    with pytest.raises(ValueError, match="不能超过 2"):
        parse_plan_steps("1. One\n2. Two\n3. Three", max_steps=2)


def test_plan_must_contain_a_step():
    with pytest.raises(ValueError, match="不能为空"):
        parse_plan_steps("  \n")
