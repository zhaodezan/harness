import pytest

from backend.app.costs import estimate_run_cost


def test_run_cost_uses_provider_token_counts_and_user_supplied_per_million_rates():
    assert estimate_run_cost(250_000, 50_000, input_rate=2.0, output_rate=8.0) == pytest.approx(0.9)


def test_run_cost_is_unavailable_without_both_rates():
    assert estimate_run_cost(100, 50, input_rate=1.0, output_rate=None) is None


def test_run_cost_rejects_negative_usage_and_rates():
    with pytest.raises(ValueError):
        estimate_run_cost(-1, 0, input_rate=1, output_rate=1)
    with pytest.raises(ValueError):
        estimate_run_cost(0, 0, input_rate=-1, output_rate=1)
