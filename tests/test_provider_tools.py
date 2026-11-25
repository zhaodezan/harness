import asyncio
import httpx

from backend.app.provider.streaming import stream_chat_events


def test_stream_chat_events_collects_tool_calls_from_deltas():
    async def run():
        async def handler(_: httpx.Request):
            payload = (
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"lookup","arguments":"{\\"key\\":"}}]}}]}\n\n'
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"alpha\\"}"}}]}}]}\n\n'
                'data: {"choices":[{"finish_reason":"tool_calls","delta":{}}]}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, text=payload)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return [event async for event in stream_chat_events(
                client, base_url="https://example.test/v1", model="m", api_key="",
                messages=[{"role": "user", "content": "lookup"}],
                tools=[{"type": "function", "function": {"name": "lookup", "description": "Lookup", "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"], "additionalProperties": False}}}],
            )]
    events = asyncio.run(run())
    assert events == [{"type": "tool_calls", "tool_calls": [{"id": "call_1", "name": "lookup", "arguments": {"key": "alpha"}}]}]
