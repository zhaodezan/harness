from decimal import Decimal


def estimate_run_cost(
    input_tokens: int,
    output_tokens: int,
    *,
    input_rate: float | None,
    output_rate: float | None,
) -> float | None:
    """Estimate currency units per run from user-configured price per 1M tokens."""
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("Token usage cannot be negative")
    for rate in (input_rate, output_rate):
        if rate is not None and rate < 0:
            raise ValueError("Token price cannot be negative")
    if input_rate is None or output_rate is None:
        return None
    cost = (Decimal(input_tokens) * Decimal(str(input_rate)) +
            Decimal(output_tokens) * Decimal(str(output_rate))) / Decimal(1_000_000)
    return float(cost.quantize(Decimal("0.00000001")))
