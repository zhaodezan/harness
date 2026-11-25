from collections.abc import AsyncIterator, Sequence
from typing import Any
import json

import httpx

from backend.app.provider.openai_compatible import ProviderError, _endpoint


async def stream_chat_events(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    model: str,
    api_key: str,
    messages: Sequence[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload: dict[str, Any] = {"model": model, "messages": list(messages), "stream": True}
    if tools:
        payload["tools"] = tools
    tool_calls: dict[int, dict[str, str]] = {}
    try:
        async with client.stream("POST", _endpoint(base_url), headers=headers, json=payload) as response:
            if response.is_error:
                if response.status_code in (401, 403):
                    detail = "模型服务认证失败"
                elif response.status_code == 429:
                    detail = "模型服务请求过于频繁"
                elif response.status_code >= 500:
                    detail = "模型服务暂时不可用"
                else:
                    detail = "模型服务拒绝了请求"
                raise ProviderError(f"{detail}（HTTP {response.status_code}）")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    if tool_calls:
                        normalized = []
                        for index in sorted(tool_calls):
                            item = tool_calls[index]
                            try:
                                arguments = json.loads(item["arguments"] or "{}")
                            except json.JSONDecodeError:
                                raise ProviderError("模型返回了无效的工具参数") from None
                            if not isinstance(arguments, dict):
                                raise ProviderError("工具参数必须是 JSON 对象")
                            normalized.append({
                                "id": item["id"], "name": item["name"],
                                "arguments": arguments,
                            })
                        yield {"type": "tool_calls", "tool_calls": normalized}
                    return
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    raise ProviderError("模型服务返回了无法识别的数据") from None
                usage = event.get("usage") or {}
                if isinstance(usage.get("prompt_tokens"), int) or isinstance(usage.get("completion_tokens"), int):
                    yield {
                        "type": "usage",
                        "input_tokens": int(usage.get("prompt_tokens") or 0),
                        "output_tokens": int(usage.get("completion_tokens") or 0),
                    }
                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if isinstance(text, str) and text:
                    yield {"type": "text", "text": text}
                for call in delta.get("tool_calls") or []:
                    index = call.get("index", 0)
                    current = tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if call.get("id"):
                        current["id"] += call["id"]
                    function = call.get("function") or {}
                    current["name"] += function.get("name", "")
                    current["arguments"] += function.get("arguments", "")
    except httpx.TimeoutException:
        raise ProviderError("连接模型服务超时") from None
    except httpx.RequestError:
        raise ProviderError("无法连接模型服务，请检查网络和服务地址") from None
