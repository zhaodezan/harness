import asyncio
import httpx

from backend.app.provider.streaming import stream_chat_events


def test_streaming_parser_surfaces_usage_when_provider_sends_it():
    async def run():
        async def handler(_: httpx.Request):
            return httpx.Response(200, text=(
                'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                'data: {"choices":[],"usage":{"prompt_tokens":17,"completion_tokens":5}}\n\n'
                'data: [DONE]\n\n'
            ))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return [event async for event in stream_chat_events(
                client, base_url="https://example.test/v1", model="m", api_key="",
                messages=[{"role": "user", "content": "hi"}],
            )]
    assert asyncio.run(run()) == [
        {"type": "text", "text": "ok"},
        {"type": "usage", "input_tokens": 17, "output_tokens": 5},
    ]
