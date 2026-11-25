import asyncio

import httpx
import pytest

from backend.app.provider.openai_compatible import ProviderError, stream_chat


def test_stream_chat_yields_text_deltas_without_network():
    async def run():
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/chat/completions"
            body = (
                'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"世界"}}]}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, text=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return [
                delta
                async for delta in stream_chat(
                    client,
                    base_url="https://example.test/v1",
                    model="test-model",
                    api_key="secret-test-key",
                    messages=[{"role": "user", "content": "你好"}],
                )
            ]

    assert asyncio.run(run()) == ["你好", "世界"]


def test_stream_chat_sanitizes_authentication_errors():
    async def run():
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="invalid secret-test-key")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderError) as error:
                async for _ in stream_chat(
                    client,
                    base_url="https://example.test/v1",
                    model="test-model",
                    api_key="secret-test-key",
                    messages=[{"role": "user", "content": "hi"}],
                ):
                    pass
        assert "secret-test-key" not in str(error.value)
        assert "401" in str(error.value)

    asyncio.run(run())
