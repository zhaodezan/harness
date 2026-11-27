"""Shared lifecycle for explicitly connected MCP servers and their registered tools."""
import asyncio
import json
from collections import defaultdict

from backend.app.db import database_connection
from backend.app.mcp import MCPError, MCPManager
from backend.app.secrets import SecretStore
from backend.app.tools import ToolDefinition, ToolRegistry

STARTUP_TIMEOUT = 12
CONNECT_TIMEOUT = 30


class MCPConnections:
    def __init__(self, database_path, secrets: SecretStore, manager: MCPManager, registry: ToolRegistry):
        self.database_path = database_path
        self.secrets = secrets
        self.manager = manager
        self.registry = registry
        self.errors: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._registered: dict[str, list[ToolDefinition]] = {}

    def _remember(self, server_id: str, enabled: bool) -> bool:
        with database_connection(self.database_path) as db:
            return bool(db.execute('UPDATE mcp_servers SET auto_connect=? WHERE id=?', (enabled, server_id)).rowcount)

    async def connect(self, server_id: str, *, confirmed: bool = False) -> list[str] | None:
        async with self._locks[server_id]:
            with database_connection(self.database_path) as db:
                row = db.execute('SELECT * FROM mcp_servers WHERE id=? AND enabled=1', (server_id,)).fetchone()
            if row is None or not (confirmed or row['auto_connect']):
                return None
            # This applies equally to manual and startup connections. Only successful
            # manual connections set the durable preference.
            registered: list[ToolDefinition] = []
            try:
                if server_id in self.manager.servers:
                    if confirmed:
                        self._remember(server_id, True)
                    return [tool['name'] for tool in self.manager.servers[server_id]['tools']]
                config = dict(row)
                config['args'] = json.loads(config.pop('args_json'))
                env_names = json.loads(config.pop('env_names_json'))
                config['env'] = {name: self.secrets.get(f'mcp:{server_id}:env:{name}') or '' for name in env_names}
                config['bearer_token'] = self.secrets.get(f'mcp:{server_id}:bearer_token') or ''
                async with asyncio.timeout(CONNECT_TIMEOUT):
                    discovered = await self.manager.connect(config)
                for item in discovered:
                    name = item['name']
                    annotation = item.get('annotations') or {}
                    definition = ToolDefinition(
                        name=f"{config['name']}/{name}",
                        description=(item.get('description') or f"MCP tool from {config['name']}")[:1000],
                        input_schema=item.get('inputSchema') or {'type': 'object', 'properties': {}, 'additionalProperties': False},
                        risk='read_only' if annotation.get('readOnlyHint') is True else 'side_effect',
                        handler=lambda arguments, remote=name: self.manager.call_tool(server_id, remote, arguments),
                        plugin_id=f'mcp:{server_id}:{name}',
                    )
                    self.registry.register(definition)
                    registered.append(definition)
                if confirmed:
                    self._remember(server_id, True)
                self._registered[server_id] = registered
                self.errors.pop(server_id, None)
                return [item['name'] for item in discovered]
            except (Exception, asyncio.CancelledError):
                self._unregister(registered)
                await self.manager.disconnect(server_id)
                raise

    def _unregister(self, tools: list[ToolDefinition]) -> None:
        for tool in tools:
            if self.registry.get_definition(tool.name) is tool:
                self.registry.unregister(tool.name)

    async def disconnect(self, server_id: str) -> bool:
        async with self._locks[server_id]:
            exists = self._remember(server_id, False)
            self.errors.pop(server_id, None)
            self._unregister(self._registered.pop(server_id, []))
            await self.manager.disconnect(server_id)
            return exists

    async def restore(self) -> None:
        with database_connection(self.database_path) as db:
            ids = [row['id'] for row in db.execute(
                'SELECT id FROM mcp_servers WHERE enabled=1 AND auto_connect=1 ORDER BY name'
            )]
        if not ids:
            return
        slots = asyncio.Semaphore(4)

        async def restore_one(server_id: str) -> None:
            try:
                async with slots:
                    await self.connect(server_id)
            except asyncio.CancelledError:
                self.errors[server_id] = '启动时连接超时，请手动重试。'
                raise
            except (TimeoutError, MCPError, OSError):
                self.errors[server_id] = '启动时未能连接，请检查服务配置后重试。'
            except Exception:
                self.errors[server_id] = '启动时未能加载 MCP 工具，请检查服务配置后重试。'

        try:
            async with asyncio.timeout(STARTUP_TIMEOUT):
                await asyncio.gather(*(restore_one(server_id) for server_id in ids))
        except TimeoutError:
            pass  # Startup remains available even if a confirmed server is unreachable.

    async def close(self) -> None:
        for tools in self._registered.values():
            self._unregister(tools)
        self._registered.clear()
        await self.manager.close()
