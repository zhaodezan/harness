import asyncio

import httpx
from httpx import ASGITransport, AsyncClient, MockTransport, Response

from backend.app.main import create_app
from model_test_utils import create_test_agent_async


class Secrets:
    def get(self, name): return "key"
    def set(self, name, value): pass
    def delete(self, name): pass


def test_second_message_for_same_session_is_rejected_while_first_run_is_active(tmp_path):
    async def run():
        provider_count = 0
        first_entered = asyncio.Event()
        second_entered = asyncio.Event()
        release_provider = asyncio.Event()

        async def provider(_: httpx.Request):
            nonlocal provider_count
            provider_count += 1
            if provider_count == 1:
                first_entered.set()
            else:
                second_entered.set()
            await release_provider.wait()
            return Response(200, text='data: {"choices":[{"delta":{"content":"done"}}]}\n\ndata: [DONE]\n\n')

        app = create_app(tmp_path / "concurrency.sqlite3", http_transport=MockTransport(provider), secret_store=Secrets())
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
                agent = (await create_test_agent_async(client, app, {"name": "Helper", "base_url": "https://example.test/v1", "model": "m"})).json()
                session = (await client.post("/api/sessions", json={"agent_id": agent["id"]})).json()
                first = asyncio.create_task(client.post(f"/api/sessions/{session['id']}/messages", json={"content": "first"}))
                await asyncio.wait_for(first_entered.wait(), 1)
                second = asyncio.create_task(client.post(f"/api/sessions/{session['id']}/messages", json={"content": "second"}))
                _, pending = await asyncio.wait({second}, timeout=0.2)
                if pending:
                    assert not second.done()
                    release_provider.set()
                    second_response = await second
                    await first
                    return second_response.status_code
                second_response = await second
                release_provider.set()
                await first
                assert second_response.status_code == 409
                return second_response.status_code

    assert asyncio.run(run()) == 409
