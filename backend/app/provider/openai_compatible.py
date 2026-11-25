import json
from collections.abc import AsyncIterator, Sequence
from typing import Any
from urllib.parse import urljoin

import httpx


class ProviderError(Exception):
    """A safe-to-display provider error without request credentials or bodies."""


def _endpoint(base_url: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", "chat/completions")


async def complete_chat(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    model: str,
    api_key: str,
    messages: Sequence[dict[str, Any]],
) -> str:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = await client.post(
            _endpoint(base_url), headers=headers,
            json={"model": model, "messages": messages, "stream": False},
        )
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
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ProviderError("模型服务返回了无法识别的数据")
        return content
    except httpx.TimeoutException:
        raise ProviderError("连接模型服务超时") from None
    except httpx.RequestError:
        raise ProviderError("无法连接模型服务，请检查网络和服务地址") from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise ProviderError("模型服务返回了无法识别的数据") from None


async def stream_chat(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    model: str,
    api_key: str,
    messages: Sequence[dict[str, Any]],
) -> AsyncIterator[str]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = {"model": model, "messages": messages, "stream": True}
    try:
        async with client.stream(
            "POST", _endpoint(base_url), headers=headers, json=payload
        ) as response:
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
                    return
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    raise ProviderError("模型服务返回了无法识别的数据") from None
                choices = event.get("choices") or []
                if not choices:
                    continue
                content = (choices[0].get("delta") or {}).get("content")
                if isinstance(content, str) and content:
                    yield content
    except httpx.TimeoutException:
        raise ProviderError("连接模型服务超时") from None
    except httpx.RequestError:
        raise ProviderError("无法连接模型服务，请检查网络和服务地址") from None
