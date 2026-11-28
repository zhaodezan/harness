"""Small, origin-pinned adapter for Dify's marketplace client protocol."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

ORIGIN = 'https://marketplace.dify.ai'
HOST = 'marketplace.dify.ai'
PAGE_SIZE = 20
MAX_PACKAGE_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
SLUG = r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}'
PLUGIN_ID = re.compile(rf'{SLUG}/{SLUG}\Z')
VERSION = r'[0-9][A-Za-z0-9.+-]{0,63}'
IDENTIFIER = re.compile(rf'(?P<id>{SLUG}/{SLUG}):(?P<version>{VERSION})@[a-zA-Z0-9]{{32,128}}\Z')


class MarketplaceError(ValueError):
    pass


def official_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return url.scheme == 'https' and url.hostname == HOST and url.port in (None, 443) and url.username is None and url.password is None
    except ValueError:
        return False


def plugin_id(value: str) -> str:
    value = value.strip()
    if official_url(value):
        path = urlsplit(value).path.rstrip('/')
        if path.startswith('/plugin/'):
            value = path.removeprefix('/plugin/')
    if not PLUGIN_ID.fullmatch(value):
        raise MarketplaceError('请输入官方市场插件详情链接或发布者/插件名。')
    return value


def localized(value: object, fallback: str = '') -> str:
    if isinstance(value, dict):
        value = next((value.get(key) for key in ('zh_Hans', 'zh_CN', 'en_US') if isinstance(value.get(key), str) and value[key]), fallback)
    return value[:2000] if isinstance(value, str) else fallback


def _project(row: object) -> dict | None:
    if not isinstance(row, dict):
        return None
    identity, version, identifier = (row.get(key) for key in ('plugin_id', 'latest_version', 'latest_package_identifier'))
    if not all(isinstance(item, str) for item in (identity, version, identifier)):
        return None
    match = IDENTIFIER.fullmatch(identifier)
    if not match or (match['id'], match['version']) != (identity, version):
        return None
    category = row.get('category')
    return {'plugin_id': identity, 'version': version, 'identifier': identifier,
            'category': category if isinstance(category, str) else '',
            'label': localized(row.get('label'), identity), 'description': localized(row.get('brief'))}


class Marketplace:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def _request(self, method: str, url: str, *, limit: int, redirects: bool = False, **kwargs) -> bytes:
        try:
            for _ in range(4):
                if not official_url(url):
                    raise MarketplaceError('市场返回的下载地址不受信任。')
                async with self.client.stream(method, url, follow_redirects=False, timeout=20, **kwargs) as response:
                    if response.is_redirect and redirects and response.headers.get('location'):
                        url = str(response.url.join(response.headers['location']))
                        kwargs.pop('params', None)
                        continue
                    if not response.is_success:
                        raise MarketplaceError('Dify 市场暂时不可用，或该版本已下架。')
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > limit:
                            raise MarketplaceError('市场响应超过大小限制。')
                        body.extend(chunk)
                    return bytes(body)
        except httpx.HTTPError:
            raise MarketplaceError('无法连接 Dify 市场，请稍后重试或导入 .difypkg。') from None
        raise MarketplaceError('市场下载重定向过多。')

    async def _rows(self, path: str, body: dict) -> tuple[list[dict], int | None]:
        import json

        data = await self._request('POST', ORIGIN + '/api/v1/plugins/' + path, limit=MAX_RESPONSE_BYTES, json=body)
        try:
            payload = json.loads(data)['data']
            items = payload['plugins']
            if not isinstance(items, list):
                raise TypeError
            rows = [value for item in items if (value := _project(item)) is not None]
            total = payload.get('total')
            return rows, total if type(total) is int else None
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise MarketplaceError('Dify 市场返回了本系统不识别的结果。') from None

    async def search(self, query: str, page: int) -> dict:
        rows, total = await self._rows('search/advanced', {
            'page': page, 'page_size': PAGE_SIZE, 'query': query, 'category': '',
            'sort_by': 'install_count', 'sort_order': 'DESC', 'tags': [], 'exclude': [], 'type': 'plugin',
        })
        return {'items': rows, 'total': max(0, total) if total is not None else len(rows), 'page_size': PAGE_SIZE}

    async def resolve(self, value: str) -> dict:
        identity = plugin_id(value)
        rows, _ = await self._rows('batch', {'plugin_ids': [identity]})
        match = next((row for row in rows if row['plugin_id'] == identity), None)
        if not match:
            raise MarketplaceError('未找到这个市场插件或没有可下载的版本。')
        return match

    async def download(self, identifier: str) -> bytes:
        if not IDENTIFIER.fullmatch(identifier):
            raise MarketplaceError('市场插件版本标识无效。')
        return await self._request('GET', ORIGIN + '/api/v1/plugins/download',
                                   limit=MAX_PACKAGE_BYTES, params={'unique_identifier': identifier}, redirects=True)
