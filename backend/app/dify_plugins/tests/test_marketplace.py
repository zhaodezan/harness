import asyncio
import json

import httpx
import pytest

from backend.app.dify_plugins.marketplace import Marketplace, MarketplaceError, plugin_id


IDENTIFIER = 'sample/echo:1.2.3@' + 'a' * 64
ROW = {'plugin_id': 'sample/echo', 'latest_version': '1.2.3', 'latest_package_identifier': IDENTIFIER,
       'category': '', 'label': {'en_US': 'Echo'}, 'brief': {'en_US': 'Echo input'}}


def test_marketplace_search_resolve_and_download():
    def reply(request):
        assert request.url.host == 'marketplace.dify.ai'
        if request.url.path.endswith('/search/advanced'):
            body = json.loads(request.content)
            assert (body['query'], body['page']) == ('echo', 2)
            assert body['category'] == ''
            return httpx.Response(200, json={'data': {'plugins': [ROW], 'total': 21}})
        if request.url.path.endswith('/batch'):
            assert json.loads(request.content) == {'plugin_ids': ['sample/echo']}
            return httpx.Response(200, json={'data': {'plugins': [ROW]}})
        assert request.url.path == '/api/v1/plugins/download'
        assert request.url.params['unique_identifier'] == IDENTIFIER
        return httpx.Response(200, content=b'package bytes')

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            market = Marketplace(client)
            assert (await market.search('echo', 2))['total'] == 21
            assert (await market.resolve('https://marketplace.dify.ai/plugin/sample/echo?language=en-US'))['identifier'] == IDENTIFIER
            assert await market.download(IDENTIFIER) == b'package bytes'

    asyncio.run(run())


@pytest.mark.parametrize('value', [
    'http://marketplace.dify.ai/plugin/sample/echo',
    'https://marketplace.dify.ai.evil.example/plugin/sample/echo',
    'https://marketplace.dify.ai@evil.example/plugin/sample/echo',
    'https://user@marketplace.dify.ai/plugin/sample/echo',
    'https://marketplace.dify.ai:222/plugin/sample/echo',
    'sample/../echo',
])
def test_rejects_nonofficial_plugin_links(value):
    with pytest.raises(MarketplaceError):
        plugin_id(value)


def test_rejects_redirect_outside_marketplace():
    def reply(request):
        assert request.url.host == 'marketplace.dify.ai'
        return httpx.Response(302, headers={'location': 'http://127.0.0.1:8765/api/models'})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            with pytest.raises(MarketplaceError, match='下载地址'):
                await Marketplace(client).download(IDENTIFIER)

    asyncio.run(run())
