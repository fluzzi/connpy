import asyncio
import logging
import os
import threading
from contextlib import suppress
from typing import Any, ClassVar

try:
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

# Silence noisy MCP and HTTP internal logging
logging.getLogger("mcp").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("httpcore").setLevel(logging.CRITICAL)


class MCPClientManager:
    """Manages MCP SSE client connections for connpy."""

    _instances: ClassVar[dict[Any, "MCPClientManager"]] = {}
    _instance: ClassVar["MCPClientManager | None"] = None
    _lock = threading.Lock()

    @classmethod
    def _resolve_scope_key(cls, config=None, *args, **kwargs) -> Any:
        cfg = config
        if cfg is None and "config" in kwargs:
            cfg = kwargs["config"]
        if cfg is not None:
            defaultdir = getattr(cfg, "defaultdir", None)
            if defaultdir:
                return os.path.abspath(str(defaultdir))
            return id(cfg)
        return "default"

    def __new__(cls, config=None, *args, **kwargs):
        key = cls._resolve_scope_key(config, *args, **kwargs)
        with cls._lock:
            if cls._instance is None and cls._instances:
                cls._instances.clear()
            if key not in cls._instances:
                instance = super().__new__(cls)
                instance._initialized = False
                instance._scope_key = key
                cls._instances[key] = instance
            cls._instance = cls._instances[key]
            return cls._instances[key]

    def __init__(self, config=None):
        if config is not None:
            self.config = config
        if getattr(self, "_initialized", False):
            return
        if not hasattr(self, "config"):
            self.config = config
        self.sessions: dict[str, dict[str, Any]] = {}  # name -> {session, stack}
        self.tool_cache: dict[str, list[dict[str, Any]]] = {}
        self._server_configs: dict[str, dict[str, Any]] = {}
        self._connecting: dict[str, asyncio.Future] = {}
        self._initialized = True

    async def get_tools_for_llm(self, os_filter: str | None = None) -> list[dict[str, Any]]:
        """Fetches tools from enabled MCP servers that match the OS filter."""
        if not MCP_AVAILABLE:
            return []

        all_llm_tools = []
        try:
            if hasattr(self.config, "get_effective_setting"):
                mcp_config = self.config.get_effective_setting("ai", {}).get("mcp_servers", {})
            else:
                mcp_config = self.config.config.get("ai", {}).get("mcp_servers", {}) if hasattr(self.config, "config") else {}
        except Exception:  # noqa: BLE001
            return []

        if not isinstance(mcp_config, dict):
            mcp_config = {}

        # Invalidate cache and reconnect if server configuration changed or server was removed
        current_names = set(mcp_config.keys())
        for old_name in list(self._server_configs.keys()):
            if old_name not in current_names:
                self.tool_cache.pop(old_name, None)
                old_data = self.sessions.pop(old_name, None)
                if old_data and old_data.get("stack"):
                    with suppress(Exception):
                        await old_data["stack"].aclose()
                self._server_configs.pop(old_name, None)

        for name, cfg in mcp_config.items():
            cached_cfg = self._server_configs.get(name)
            if cached_cfg is not None and cached_cfg != cfg:
                self.tool_cache.pop(name, None)
                old_data = self.sessions.pop(name, None)
                if old_data and old_data.get("stack"):
                    with suppress(Exception):
                        await old_data["stack"].aclose()
            self._server_configs[name] = dict(cfg) if isinstance(cfg, dict) else cfg

        async def _fetch(name, cfg):
            if not isinstance(cfg, dict) or not cfg.get("enabled", True):
                return []

            # Filter by OS if specified in config (primarily used for copilot strict matching)
            auto_os = cfg.get("auto_load_on_os")
            if os_filter is not None and auto_os and os_filter.lower() != auto_os.lower():
                return []

            with suppress(Exception):
                session = await self._ensure_connected(name, cfg)
                if session:
                    if name in self.tool_cache:
                        return self.tool_cache[name]
                    llm_tools = await self._fetch_tools_as_openai(name, session)
                    self.tool_cache[name] = llm_tools
                    return llm_tools
            return []

        tasks = [_fetch(name, cfg) for name, cfg in mcp_config.items()]

        if tasks:
            results = await asyncio.gather(*tasks)
            for tools in results:
                all_llm_tools.extend(tools)

        return all_llm_tools

    async def _ensure_connected(self, name: str, cfg: dict[str, Any]) -> Any | None:
        if not MCP_AVAILABLE:
            return None

        if name in self.sessions and self.sessions[name].get("session"):
            return self.sessions[name]["session"]

        url = cfg.get("url")
        if not url:
            return None

        if name in self._connecting:
            try:
                return await asyncio.wait_for(asyncio.shield(self._connecting[name]), timeout=10.0)
            except Exception:  # noqa: BLE001
                return None

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._connecting[name] = fut

        try:
            from contextlib import AsyncExitStack
            stack = AsyncExitStack()

            async def _do_connect():
                read, write = await stack.enter_async_context(sse_client(url))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                return session

            session = await asyncio.wait_for(_do_connect(), timeout=15.0)
            self.sessions[name] = {"session": session, "stack": stack}
            fut.set_result(session)
            return session
        except Exception:  # noqa: BLE001
            fut.set_result(None)
            return None
        finally:
            if name in self._connecting:
                del self._connecting[name]

    async def _fetch_tools_as_openai(self, server_name: str, session: Any) -> list[dict[str, Any]]:
        try:
            result = await asyncio.wait_for(session.list_tools(), timeout=5.0)
            openai_tools = []
            for tool in result.tools:
                prefixed_name = f"mcp_{server_name}__{tool.name}"
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": prefixed_name,
                        "description": f"[{server_name}] {tool.description}",
                        "parameters": tool.inputSchema,
                    },
                })
            return openai_tools
        except Exception:  # noqa: BLE001
            return []

    async def call_tool(self, full_tool_name: str, arguments: dict[str, Any]) -> Any:
        """Calls an MCP tool and returns text result."""
        if not MCP_AVAILABLE:
            return "Error: MCP SDK is not installed."

        if "__" not in full_tool_name:
            return f"Error: Tool {full_tool_name} is not a valid MCP tool."

        clean_name = full_tool_name.removeprefix("mcp_")
        server_name, tool_name = clean_name.split("__", 1)

        if server_name not in self.sessions:
            return f"Error: MCP server {server_name} is not connected."

        session = self.sessions[server_name]["session"]
        try:
            result = await asyncio.wait_for(session.call_tool(tool_name, arguments), timeout=60.0)
            text_outputs = [content.text for content in result.content if hasattr(content, "text")]
            return "\n".join(text_outputs) if text_outputs else str(result)
        except Exception as e:  # noqa: BLE001
            return f"Error calling tool {tool_name} on {server_name}: {e!s}"

    async def shutdown(self):
        """Close all SSE connections and clean up resources."""
        for data in list(self.sessions.values()):
            stack = data.get("stack")
            if stack:
                with suppress(Exception):
                    await stack.aclose()
        self.sessions.clear()
        self.tool_cache.clear()
        self._server_configs.clear()
        for fut in list(self._connecting.values()):
            if not fut.done():
                fut.cancel()
        self._connecting.clear()
        with self._lock:
            if hasattr(self, "_scope_key") and self._scope_key in self._instances:
                del self._instances[self._scope_key]
        self._initialized = False
