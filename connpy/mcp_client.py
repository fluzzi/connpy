import asyncio
import json
import os
import threading
from typing import Any, Dict, List, Optional
import logging

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
    
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(MCPClientManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, config=None):
        if self._initialized:
            return
        self.config = config
        self.sessions: Dict[str, Dict[str, Any]] = {} # name -> {session, stack}
        self.tool_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._connecting: Dict[str, asyncio.Future] = {}
        self._initialized = True

    async def get_tools_for_llm(self, os_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetches tools from enabled MCP servers that match the OS filter.
        """
        if not MCP_AVAILABLE:
            return []

        all_llm_tools = []
        try:
            if hasattr(self.config, "get_effective_setting"):
                mcp_config = self.config.get_effective_setting("ai", {}).get("mcp_servers", {})
            else:
                mcp_config = self.config.config.get("ai", {}).get("mcp_servers", {}) if hasattr(self.config, "config") else {}
        except Exception:
            return []
        
        async def _fetch(name, cfg):
            if not cfg.get("enabled", True): return []
            
            # Filter by OS if specified in config (primarily used for copilot strict matching)
            auto_os = cfg.get("auto_load_on_os")
            if os_filter is not None and auto_os and os_filter.lower() != auto_os.lower():
                return []

            try:
                session = await self._ensure_connected(name, cfg)
                if session:
                    if name in self.tool_cache: return self.tool_cache[name]
                    llm_tools = await self._fetch_tools_as_openai(name, session)
                    self.tool_cache[name] = llm_tools
                    return llm_tools
            except Exception:
                pass
            return []

        tasks = [ _fetch(name, cfg) for name, cfg in mcp_config.items() ]
        
        if tasks:
            results = await asyncio.gather(*tasks)
            for tools in results:
                all_llm_tools.extend(tools)
                
        return all_llm_tools

    async def _ensure_connected(self, name: str, cfg: Dict[str, Any]) -> Optional[Any]:
        if not MCP_AVAILABLE: return None

        if name in self.sessions and self.sessions[name].get("session"):
            return self.sessions[name]["session"]

        url = cfg.get("url")
        if not url:
            return None

        if name in self._connecting:
            try:
                return await asyncio.wait_for(asyncio.shield(self._connecting[name]), timeout=10.0)
            except Exception:
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
        except Exception:
            fut.set_result(None)
            return None
        finally:
            if name in self._connecting:
                del self._connecting[name]

    async def _fetch_tools_as_openai(self, server_name: str, session: Any) -> List[Dict[str, Any]]:
        try:
            result = await asyncio.wait_for(session.list_tools(), timeout=5.0)
            openai_tools = []
            for tool in result.tools:
                # Use mcp_ prefix to ensure valid function name for LiteLLM/Gemini
                prefixed_name = f"mcp_{server_name}__{tool.name}"
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": prefixed_name,
                        "description": f"[{server_name}] {tool.description}",
                        "parameters": tool.inputSchema
                    }
                })
            return openai_tools
        except Exception:
            return []

    async def call_tool(self, full_tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Calls an MCP tool and returns text result."""
        if not MCP_AVAILABLE:
            return "Error: MCP SDK is not installed."

        if "__" not in full_tool_name:
            return f"Error: Tool {full_tool_name} is not a valid MCP tool."
            
        clean_name = full_tool_name[4:] if full_tool_name.startswith("mcp_") else full_tool_name
        server_name, tool_name = clean_name.split("__", 1)
        
        if server_name not in self.sessions:
            return f"Error: MCP server {server_name} is not connected."
            
        session = self.sessions[server_name]["session"]
        try:
            result = await asyncio.wait_for(session.call_tool(tool_name, arguments), timeout=60.0)
            text_outputs = [content.text for content in result.content if hasattr(content, "text")]
            return "\n".join(text_outputs) if text_outputs else str(result)
        except Exception as e:
            return f"Error calling tool {tool_name} on {server_name}: {str(e)}"

    async def shutdown(self):
        """Close all SSE connections."""
        for name, data in self.sessions.items():
            stack = data.get("stack")
            if stack:
                await stack.aclose()
        self.sessions = {}
