from backend.app.provider.openai_compatible import ProviderError


class MaxRoundsExceeded(ProviderError):
    """Raised before a model request would exceed the configured turn budget."""


class ModelRoundCounter:
    def __init__(self, limit: int):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit not in {-1, *range(1, 10001)}:
            raise ValueError('最大轮次必须为 -1 或 1 到 10000 的整数')
        self.limit = limit
        self.calls = 0

    def start_call(self) -> int:
        if self.limit != -1 and self.calls >= self.limit:
            raise MaxRoundsExceeded(f'已达到本次请求的最大模型轮次（{self.limit}），可提高限制或设置为 -1 无限运行。')
        self.calls += 1
        return self.calls
