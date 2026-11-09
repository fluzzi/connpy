import contextlib
import logging
import re
import sys
from typing import Any, ClassVar

import yaml

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP as MCPServer
    except ImportError:
        MCPServer = None

logger = logging.getLogger("connpy.mcp_server")


class ConnpyMCPServer:
    """Native Model Context Protocol (MCP) Server for connpy."""

    SAFE_COMMANDS: ClassVar[list[str]] = [
        r"^show\s+",
        r"^ls\s*",
        r"^cat\s+",
        r"^ip\s+",
        r"^pwd$",
        r"^hostname$",
        r"^uname",
        r"^df\s*",
        r"^free\s*",
        r"^ps\s*",
        r"^ping\s+",
        r"^traceroute\s+",
        r"^whois\s+",
        r"^kubectl\s+(get|describe|version|logs|top|explain|cluster-info|api-resources|api-versions)\s+",
        r"^systemctl\s+status\s+",
        r"^journalctl\s+",
    ]

    def __init__(self, config=None, services=None, trust: bool = False):
        self.config = config
        self.services = services
        if self.services is None:
            if self.config is not None:
                from .services.provider import ServiceProvider

                self.services = ServiceProvider(self.config)
        elif self.config is None and hasattr(self.services, "config"):
            self.config = self.services.config

        self.trust = bool(trust)

        # Build list of safe commands (SAFE_COMMANDS + trusted_commands from config)
        aiconfig = {}
        if self.config:
            if hasattr(self.config, "get_effective_setting"):
                aiconfig = self.config.get_effective_setting("ai", {})
            elif hasattr(self.config, "config") and isinstance(self.config.config, dict):
                aiconfig = self.config.config.get("ai", {})

        custom_trusted = aiconfig.get("trusted_commands", []) if isinstance(aiconfig, dict) else []
        if isinstance(custom_trusted, str):
            custom_trusted = [c.strip() for c in custom_trusted.split(",") if c.strip()]
        self.safe_commands = list(self.SAFE_COMMANDS) + (
            custom_trusted if isinstance(custom_trusted, list) else []
        )

        # Initialize MCP Server
        if MCPServer is not None:
            self.server = MCPServer("connpy")
            self._register_tools()
        else:
            self.server = None

    def _register_tools(self):
        """Register the 3 tiers of MCP tools."""
        if not self.server:
            return

        # Level 1 Tools (Read-Only)
        self.server.tool()(self.list_nodes)
        self.server.tool()(self.list_folders)
        self.server.tool()(self.get_node_info)
        self.server.tool()(self.list_profiles)
        self.server.tool()(self.get_active_context)

        # Level 2 Tools (Execution, Testing, Playbooks)
        self.server.tool()(self.run_commands)
        self.server.tool()(self.test_commands)
        self.server.tool()(self.validate_playbook)
        self.server.tool()(self.run_playbook)

        # Level 3 Tools (CRUD - Requires trust=True)
        self.server.tool()(self.add_node)
        self.server.tool()(self.update_node)
        self.server.tool()(self.delete_node)

    # -------------------------------------------------------------------------
    # Security Validation
    # -------------------------------------------------------------------------

    def is_safe_command(self, cmd: str) -> bool:
        """Validate if a command is considered safe under current trust settings.

        - If trust is True, all commands are allowed.
        - Block chaining/injection characters: ';', '&&', '||', '|', '`', '$(', '>', '<', '\\n', '\\r', '&'.
        - Validates against self.safe_commands (SAFE_COMMANDS + trusted_commands).
        """
        if self.trust:
            return True

        if not isinstance(cmd, str):
            cmd = str(cmd)

        # Check for shell chaining and redirection injection tokens
        injection_tokens = [";", "&&", "||", "|", "`", "$(", ">", "<", "\n", "\r", "&"]
        if any(tok in cmd for tok in injection_tokens):
            return False

        clean_cmd = cmd.strip()
        return any(re.match(pattern, clean_cmd, re.IGNORECASE) for pattern in self.safe_commands)

    def _mask_secrets(self, val: Any) -> Any:
        """Recursively mask passwords, passphrases, secrets, and auth tokens with '***'."""
        if isinstance(val, dict):
            masked = {}
            for k, v in val.items():
                k_lower = str(k).lower()
                if any(sec in k_lower for sec in ["password", "passphrase", "secret", "token"]):
                    masked[k] = "***"
                elif isinstance(v, (dict, list)):
                    masked[k] = self._mask_secrets(v)
                else:
                    masked[k] = v
            return masked
        if isinstance(val, list):
            return [self._mask_secrets(item) for item in val]
        return val

    # -------------------------------------------------------------------------
    # Level 1: Read-Only Inventory and Context Tools
    # -------------------------------------------------------------------------

    def list_nodes(self, filter_pattern: str = "", format_str: str = "") -> list[str]:
        """List inventory nodes, optionally filtered by regex pattern and formatted."""
        if not self.services or not hasattr(self.services, "nodes"):
            return []
        f = filter_pattern if filter_pattern else None
        fmt = format_str if format_str else None
        return self.services.nodes.list_nodes(filter_str=f, format_str=fmt)

    def list_folders(self, filter_pattern: str = "") -> list[str]:
        """List inventory folders, optionally filtered by regex pattern."""
        if not self.services or not hasattr(self.services, "nodes"):
            return []
        f = filter_pattern if filter_pattern else None
        return self.services.nodes.list_folders(filter_str=f)

    def get_node_info(self, node_name: str) -> dict:
        """Get full configuration metadata for a specific node.

        Passwords, passphrases, and secrets are ALWAYS masked with '***'.
        """
        if not self.services or not hasattr(self.services, "nodes"):
            return {"error": "Node service unavailable."}
        try:
            details = self.services.nodes.get_node_details(node_name)
            if hasattr(self.services, "profiles"):
                with contextlib.suppress(Exception):
                    details = self.services.profiles.resolve_node_data(details)
            return self._mask_secrets(details)
        except Exception as e:  # noqa: BLE001
            return {"error": f"Node '{node_name}' not found: {e}"}

    def list_profiles(self, filter_pattern: str = "") -> list[str]:
        """List all available node connection profiles."""
        if not self.services or not hasattr(self.services, "profiles"):
            return []
        f = filter_pattern if filter_pattern else None
        return self.services.profiles.list_profiles(filter_str=f)

    def get_active_context(self) -> dict:
        """Get the currently active context name and its matching regex patterns."""
        if not self.services or not hasattr(self.services, "context"):
            return {"context": "all", "patterns": [".*"]}
        try:
            ctx_name = self.services.context.current_context
            patterns = [p.pattern for p in self.services.context.get_active_regexes()]
            return {"context": ctx_name, "patterns": patterns}
        except Exception as e:  # noqa: BLE001
            return {"context": "unknown", "error": str(e)}

    # -------------------------------------------------------------------------
    # Level 2: Execution, Testing, and Playbooks Tools
    # -------------------------------------------------------------------------

    def run_commands(
        self,
        nodes_filter: str,
        commands: list[str] | str,
        parallel: bool = True,
        timeout: int = 30,
    ) -> dict:
        """Execute commands on nodes matching the filter pattern.

        Unsafe commands are blocked unless trust mode is enabled.
        """
        if not self.services or not hasattr(self.services, "execution"):
            return {"success": False, "error": "Execution service unavailable."}

        # Normalize commands list
        if isinstance(commands, str):
            try:
                import json

                parsed = json.loads(commands)
                commands = parsed if isinstance(parsed, list) else [commands]
            except Exception:  # noqa: BLE001
                commands = [c.strip() for c in commands.splitlines() if c.strip()]
        elif not isinstance(commands, list):
            commands = [str(commands)]

        # Check safety
        if not self.trust:
            unsafe = [cmd for cmd in commands if not self.is_safe_command(cmd)]
            if unsafe:
                return {
                    "success": False,
                    "error": f"Permission denied: unsafe commands detected: {unsafe}. Requires trust=True.",
                }

        par_count = 10 if parallel is True else (parallel if isinstance(parallel, int) else 1)
        try:
            results = self.services.execution.run_commands(
                nodes_filter=nodes_filter,
                commands=commands,
                parallel=par_count,
                timeout=timeout,
            )
            return {"success": True, "results": results}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def test_commands(
        self,
        nodes_filter: str,
        commands: list[str] | str,
        expected: list[str] | str = "",
        parallel: bool = True,
        timeout: int = 30,
    ) -> dict:
        """Execute commands and assert expected patterns on nodes matching the filter."""
        if not self.services or not hasattr(self.services, "execution"):
            return {"success": False, "error": "Execution service unavailable."}

        # Normalize commands
        if isinstance(commands, str):
            try:
                import json

                parsed = json.loads(commands)
                commands = parsed if isinstance(parsed, list) else [commands]
            except Exception:  # noqa: BLE001
                commands = [c.strip() for c in commands.splitlines() if c.strip()]
        elif not isinstance(commands, list):
            commands = [str(commands)]

        # Normalize expected
        if isinstance(expected, str):
            try:
                import json

                parsed = json.loads(expected)
                expected = parsed if isinstance(parsed, list) else ([expected] if expected else [])
            except Exception:  # noqa: BLE001
                expected = [expected] if expected else []
        elif not isinstance(expected, list):
            expected = [str(expected)] if expected else []

        # Check safety
        if not self.trust:
            unsafe = [cmd for cmd in commands if not self.is_safe_command(cmd)]
            if unsafe:
                return {
                    "success": False,
                    "error": f"Permission denied: unsafe commands detected: {unsafe}. Requires trust=True.",
                }

        par_count = 10 if parallel is True else (parallel if isinstance(parallel, int) else 1)
        try:
            results = self.services.execution.test_commands(
                nodes_filter=nodes_filter,
                commands=commands,
                expected=expected,
                parallel=par_count,
                timeout=timeout,
            )
            return {"success": True, "results": results}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def validate_playbook(self, playbook_yaml: str) -> dict:
        """Validate the syntax, schema, and tasks of a Connpy YAML playbook."""
        try:
            data = yaml.safe_load(playbook_yaml)
        except Exception as e:  # noqa: BLE001
            return {"valid": False, "error": f"YAML Syntax Error: {e}"}

        if not isinstance(data, dict):
            return {"valid": False, "error": "Playbook must be a YAML dictionary."}

        if "tasks" not in data:
            return {"valid": False, "error": "Playbook missing mandatory root 'tasks' key."}

        tasks = data["tasks"]
        if not isinstance(tasks, list):
            return {"valid": False, "error": "'tasks' must be a list of tasks."}

        for idx, task in enumerate(tasks):
            if not isinstance(task, dict):
                return {"valid": False, "error": f"Task index {idx} must be a dictionary."}

            name = task.get("name", f"Task {idx}")
            mandatory = ["name", "action", "nodes", "commands", "output"]
            missing = [f for f in mandatory if f not in task]
            if missing:
                return {
                    "valid": False,
                    "error": f"Task '{name}' (index {idx}) is missing mandatory fields: {missing}",
                }

            nodes = task["nodes"]
            if not isinstance(nodes, (str, list)):
                return {
                    "valid": False,
                    "error": f"Task '{name}' (index {idx}) 'nodes' must be a string or list of strings.",
                }

            if isinstance(nodes, list):
                for n_idx, node_item in enumerate(nodes):
                    if not isinstance(node_item, str):
                        return {
                            "valid": False,
                            "error": f"Task '{name}' (index {idx}) 'nodes' contains non-string at index {n_idx}: {node_item}",
                        }

            action = task["action"]
            if action not in ["run", "test"]:
                return {
                    "valid": False,
                    "error": f"Task '{name}' (index {idx}) invalid action '{action}'. Choices: 'run', 'test'.",
                }

            if action == "test" and "expected" not in task:
                return {
                    "valid": False,
                    "error": f"Task '{name}' (index {idx}) action 'test' requires 'expected' key.",
                }

            output = task["output"]
            if output not in [None, "stdout", "null"] and not str(output).startswith("/"):
                return {
                    "valid": False,
                    "error": f"Task '{name}' (index {idx}) output '{output}' must be 'stdout', 'null' or an absolute path.",
                }

        return {"valid": True, "message": "Playbook schema and syntax is valid."}

    def run_playbook(self, playbook_yaml: str) -> dict:
        """Validate and execute a playbook, verifying commands beforehand if trust=False."""
        val = self.validate_playbook(playbook_yaml)
        if not val.get("valid"):
            return {"success": False, "error": f"Invalid playbook: {val.get('error')}"}

        data = yaml.safe_load(playbook_yaml)
        tasks = data.get("tasks", [])

        # Pre-verify commands if not trusted
        if not self.trust:
            for idx, task in enumerate(tasks):
                name = task.get("name", f"Task {idx}")
                commands = task.get("commands", [])
                if isinstance(commands, str):
                    commands = [commands]
                unsafe = [cmd for cmd in commands if not self.is_safe_command(cmd)]
                if unsafe:
                    return {
                        "success": False,
                        "error": f"Permission denied: unsafe commands detected in task '{name}': {unsafe}. Requires trust=True.",
                    }

        # Execute tasks
        results = {}
        for idx, task in enumerate(tasks):
            name = task.get("name", f"task_{idx}")
            action = task.get("action")
            nodes = task.get("nodes")
            commands = task.get("commands")
            if isinstance(commands, str):
                commands = [commands]

            try:
                if action == "run":
                    res = self.services.execution.run_commands(nodes_filter=nodes, commands=commands)
                elif action == "test":
                    expected = task.get("expected", [])
                    if isinstance(expected, str):
                        expected = [expected]
                    res = self.services.execution.test_commands(
                        nodes_filter=nodes, commands=commands, expected=expected
                    )
                else:
                    res = f"Unsupported action: {action}"
                results[name] = res
            except Exception as e:  # noqa: BLE001
                return {
                    "success": False,
                    "error": f"Execution failed at task '{name}': {e}",
                    "partial_results": results,
                }

        return {"success": True, "results": results}

    # -------------------------------------------------------------------------
    # Level 3: Inventory Mutation Tools (CRUD - Requires trust=True)
    # -------------------------------------------------------------------------

    def add_node(
        self, unique_id: str, data: dict | None = None, is_folder: bool = False, **kwargs
    ) -> dict:
        """Add a new node or folder to the inventory (requires trust=True)."""
        if not self.trust:
            return {
                "success": False,
                "error": "Permission denied: CRUD operations require trust mode (--trust).",
            }

        if not self.services or not hasattr(self.services, "nodes"):
            return {"success": False, "error": "Node service unavailable."}

        node_data = (data or {}).copy()
        node_data.update(kwargs)

        try:
            self.services.nodes.add_node(unique_id, node_data, is_folder=is_folder)
            return {
                "success": True,
                "message": f"{'Folder' if is_folder else 'Node'} '{unique_id}' created successfully.",
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def update_node(self, unique_id: str, updates: dict | None = None, **kwargs) -> dict:
        """Update an existing node configuration (requires trust=True)."""
        if not self.trust:
            return {
                "success": False,
                "error": "Permission denied: CRUD operations require trust mode (--trust).",
            }

        if not self.services or not hasattr(self.services, "nodes"):
            return {"success": False, "error": "Node service unavailable."}

        node_updates = (updates or {}).copy()
        node_updates.update(kwargs)

        try:
            self.services.nodes.update_node(unique_id, node_updates)
            return {"success": True, "message": f"Node '{unique_id}' updated successfully."}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def delete_node(self, unique_id: str, is_folder: bool = False) -> dict:
        """Delete a node or folder from the inventory (requires trust=True)."""
        if not self.trust:
            return {
                "success": False,
                "error": "Permission denied: CRUD operations require trust mode (--trust).",
            }

        if not self.services or not hasattr(self.services, "nodes"):
            return {"success": False, "error": "Node service unavailable."}

        try:
            self.services.nodes.delete_node(unique_id, is_folder=is_folder)
            return {
                "success": True,
                "message": f"{'Folder' if is_folder else 'Node'} '{unique_id}' deleted successfully.",
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    # -------------------------------------------------------------------------
    # Server Execution and Async Helper
    # -------------------------------------------------------------------------

    async def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        """Call an MCP tool asynchronously via the underlying MCP server."""
        if not self.server:
            raise RuntimeError("MCP SDK is not initialized.")
        return await self.server.call_tool(name, arguments or {})

    def run(self, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000, **kwargs):
        """Run the MCP server using either stdio or sse transport."""
        if not self.server:
            raise RuntimeError("MCP server cannot start: 'mcp' package is not installed.")

        if transport == "stdio":
            # Redirect logs and printer outputs to sys.stderr for JSON-RPC
            with contextlib.suppress(Exception):
                from . import printer

                printer.set_thread_stream(sys.stderr)

            for handler in logging.root.handlers:
                if hasattr(handler, "stream") and handler.stream == sys.stdout:
                    handler.stream = sys.stderr
            logging.basicConfig(stream=sys.stderr, level=logging.INFO)

            return self.server.run(transport="stdio", **kwargs)

        if transport == "sse":
            if hasattr(self.server, "settings"):
                if hasattr(self.server.settings, "host"):
                    self.server.settings.host = host
                if hasattr(self.server.settings, "port"):
                    self.server.settings.port = port
            try:
                return self.server.run(transport="sse", host=host, port=port, **kwargs)
            except TypeError:
                return self.server.run(transport="sse", **kwargs)

        msg = f"Unknown transport: {transport}. Supported transports: 'stdio', 'sse'"
        raise ValueError(msg)
