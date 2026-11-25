import asyncio

import httpx

from backend.app.provider.openai_compatible import complete_chat


def test_complete_chat_normalizes_openai_compatible_message():
    async def run():
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": "1. Inspect\n2. Report"}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await complete_chat(
                client, base_url="https://example.test/v1", model="model",
                api_key="", messages=[{"role": "user", "content": "Plan"}],
            )
    assert asyncio.run(run()) == "1. Inspect\n2. Report"
