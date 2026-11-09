"""Tests for ConnpyMCPServer (spec-03-mcp-server)."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from connpy.mcp_server import ConnpyMCPServer
from connpy.services.provider import ServiceProvider


@pytest.fixture
def services(populated_config):
    """Provide ServiceProvider initialized with sample connections/profiles."""
    return ServiceProvider(populated_config)


@pytest.fixture
def mcp_server(populated_config, services):
    """Provide ConnpyMCPServer in untrusted mode."""
    return ConnpyMCPServer(config=populated_config, services=services, trust=False)


@pytest.fixture
def trusted_mcp_server(populated_config, services):
    """Provide ConnpyMCPServer in trusted mode."""
    return ConnpyMCPServer(config=populated_config, services=services, trust=True)


# =============================================================================
# Level 1 (Read-Only) Tools & Password Masking Tests
# =============================================================================

class TestLevel1Tools:
    def test_list_nodes_all(self, mcp_server):
        nodes = mcp_server.list_nodes()
        assert isinstance(nodes, list)
        assert "router1" in nodes
        assert "server1@office" in nodes
        assert "db1@datacenter@office" in nodes

    def test_list_nodes_filtered(self, mcp_server):
        nodes = mcp_server.list_nodes(filter_pattern="router.*")
        assert "router1" in nodes
        assert "server1@office" not in nodes

    def test_list_nodes_formatted(self, mcp_server):
        fmt = "{name} - {location}"
        formatted = mcp_server.list_nodes(filter_pattern="router1", format_str=fmt)
        assert len(formatted) == 1
        assert formatted[0] == "router1 - root"

    def test_list_folders(self, mcp_server):
        folders = mcp_server.list_folders()
        assert "@office" in folders
        assert "@datacenter@office" in folders

    def test_list_folders_filtered(self, mcp_server):
        folders = mcp_server.list_folders(filter_pattern="datacenter")
        assert "@datacenter@office" in folders
        assert "@office" not in folders

    def test_get_node_info_masks_password(self, mcp_server):
        info = mcp_server.get_node_info("router1")
        assert isinstance(info, dict)
        assert info["host"] == "10.0.0.1"
        assert info["user"] == "admin"
        # CRITICAL requirement: ALWAYS mask password/passphrase/secret with "***"
        assert info["password"] == "***"
        assert "pass1" not in str(info)

    def test_get_node_info_masks_nested_secrets(self, mcp_server):
        test_data = {
            "password": "plain_password",
            "secret": "enable_secret",
            "passphrase": "ssh_passphrase",
            "api_token": "bearer_token",
            "nested": {
                "admin_password": "secret_nested",
                "normal_key": "safe_value",
            },
        }
        masked = mcp_server._mask_secrets(test_data)
        assert masked["password"] == "***"
        assert masked["secret"] == "***"
        assert masked["passphrase"] == "***"
        assert masked["api_token"] == "***"
        assert masked["nested"]["admin_password"] == "***"
        assert masked["nested"]["normal_key"] == "safe_value"

    def test_get_node_info_not_found(self, mcp_server):
        res = mcp_server.get_node_info("non_existent_node")
        assert "error" in res
        assert "not found" in res["error"].lower()

    def test_list_profiles(self, mcp_server):
        profiles = mcp_server.list_profiles()
        assert "default" in profiles
        assert "office-user" in profiles

    def test_list_profiles_filtered(self, mcp_server):
        profiles = mcp_server.list_profiles(filter_pattern="office")
        assert "office-user" in profiles
        assert "default" not in profiles

    def test_get_active_context(self, mcp_server):
        ctx = mcp_server.get_active_context()
        assert isinstance(ctx, dict)
        assert "context" in ctx
        assert ctx["context"] == "all"
        assert "patterns" in ctx
        assert ".*" in ctx["patterns"]


# =============================================================================
# Level 2 Security & Execution Tests (is_safe_command, run, test, playbooks)
# =============================================================================

class TestLevel2Tools:
    def test_is_safe_command_allowed_patterns(self, mcp_server):
        safe_samples = [
            "show version",
            "show ip route",
            "ls -la /var/log",
            "cat /etc/hosts",
            "ip address",
            "pwd",
            "hostname",
            "uname -a",
            "df -h",
            "free -m",
            "ps aux",
            "ping -c 4 8.8.8.8",
            "traceroute 1.1.1.1",
            "whois example.com",
            "kubectl get pods -n default",
            "systemctl status nginx",
            "journalctl -u ssh -n 50",
        ]
        for cmd in safe_samples:
            assert mcp_server.is_safe_command(cmd) is True, f"Expected safe: {cmd}"

    def test_is_safe_command_blocks_unsafe_in_untrusted_mode(self, mcp_server):
        unsafe_samples = [
            "reboot",
            "rm -rf /",
            "shutdown -h now",
            "configure terminal",
            "set system host-name bad",
            "echo hacked",
            "dd if=/dev/zero of=/dev/sda",
        ]
        for cmd in unsafe_samples:
            assert mcp_server.is_safe_command(cmd) is False, f"Expected unsafe: {cmd}"

    def test_is_safe_command_blocks_injection_characters(self, mcp_server):
        injection_samples = [
            "show version; rm -rf /",
            "show version && reboot",
            "show version || reboot",
            "cat /etc/hosts | grep root",
            "show `whoami`",
            "cat $(id)",
            "echo test > /tmp/bad",
            "cat < /etc/shadow",
            "show ip\nreboot",
            "show ip\rreboot",
            "show version & reboot",
        ]
        for cmd in injection_samples:
            assert not mcp_server.is_safe_command(cmd), f"Injection allowed: {cmd}"

    def test_is_safe_command_allows_all_in_trusted_mode(self, trusted_mcp_server):
        commands = [
            "reboot",
            "rm -rf /",
            "configure terminal",
            "show version; reboot",
            "cat /etc/hosts | grep foo",
        ]
        for cmd in commands:
            assert trusted_mcp_server.is_safe_command(cmd) is True

    def test_run_commands_blocks_unsafe_when_untrusted(self, mcp_server):
        res = mcp_server.run_commands("router1", ["show version", "reboot"])
        assert res["success"] is False
        assert "Permission denied" in res["error"]
        assert "reboot" in res["error"]

    def test_run_commands_executes_when_trusted(self, trusted_mcp_server):
        exec_svc = trusted_mcp_server.services.execution
        with patch.object(exec_svc, "run_commands") as mock_exec:
            mock_exec.return_value = {"router1": {"output": "ok", "status": 0}}
            res = trusted_mcp_server.run_commands("router1", ["reboot"])
            assert res["success"] is True
            assert res["results"]["router1"]["output"] == "ok"
            mock_exec.assert_called_once()

    def test_test_commands_blocks_unsafe_when_untrusted(self, mcp_server):
        res = mcp_server.test_commands("router1", ["rm -rf /"], expected=["root"])
        assert res["success"] is False
        assert "Permission denied" in res["error"]

    def test_test_commands_executes_when_trusted(self, trusted_mcp_server):
        exec_svc = trusted_mcp_server.services.execution
        with patch.object(exec_svc, "test_commands") as mock_test:
            mock_test.return_value = {"router1": {"rm -rf /": True}}
            res = trusted_mcp_server.test_commands(
                "router1", ["rm -rf /"], expected=["root"]
            )
            assert res["success"] is True
            assert res["results"]["router1"]["rm -rf /"] is True

    def test_validate_playbook_valid(self, mcp_server):
        valid_yaml = """
tasks:
  - name: Check Status
    action: run
    nodes: router1
    commands:
      - show version
    output: stdout
  - name: Assert Interface
    action: test
    nodes: router1
    commands:
      - show ip int brief
    expected:
      - GigabitEthernet0/0
    output: stdout
"""
        res = mcp_server.validate_playbook(valid_yaml)
        assert res["valid"] is True

    def test_validate_playbook_invalid(self, mcp_server):
        # Missing root tasks key
        res1 = mcp_server.validate_playbook("name: invalid")
        assert res1["valid"] is False
        assert "tasks" in res1["error"]

        # Missing expected in test action
        invalid_yaml = """
tasks:
  - name: Bad Test
    action: test
    nodes: router1
    commands:
      - show version
    output: stdout
"""
        res2 = mcp_server.validate_playbook(invalid_yaml)
        assert res2["valid"] is False
        assert "expected" in res2["error"]

    def test_run_playbook_blocks_unsafe_commands_when_untrusted(self, mcp_server):
        unsafe_playbook = """
tasks:
  - name: Reboot Device
    action: run
    nodes: router1
    commands:
      - reboot
    output: stdout
"""
        res = mcp_server.run_playbook(unsafe_playbook)
        assert res["success"] is False
        assert "Permission denied" in res["error"]
        assert "reboot" in res["error"]

    def test_run_playbook_executes_when_trusted(self, trusted_mcp_server):
        playbook = """
tasks:
  - name: Safe Task
    action: run
    nodes: router1
    commands:
      - reboot
    output: stdout
"""
        exec_svc = trusted_mcp_server.services.execution
        with patch.object(exec_svc, "run_commands") as mock_exec:
            mock_exec.return_value = {"router1": {"output": "ok", "status": 0}}
            res = trusted_mcp_server.run_playbook(playbook)
            assert res["success"] is True
            assert "Safe Task" in res["results"]


# =============================================================================
# Level 3 (CRUD) Inventory Mutation Tests
# =============================================================================

class TestLevel3CRUD:
    def test_add_node_blocked_when_untrusted(self, mcp_server):
        res = mcp_server.add_node("router99", {"host": "10.0.0.99"})
        assert res["success"] is False
        assert "Permission denied" in res["error"]

    def test_add_node_allowed_when_trusted(self, trusted_mcp_server):
        data = {"host": "10.0.0.99", "protocol": "ssh"}
        res = trusted_mcp_server.add_node("router99", data)
        assert res["success"] is True
        nodes = trusted_mcp_server.list_nodes()
        assert "router99" in nodes

    def test_update_node_blocked_when_untrusted(self, mcp_server):
        res = mcp_server.update_node("router1", {"host": "10.0.0.254"})
        assert res["success"] is False
        assert "Permission denied" in res["error"]

    def test_update_node_allowed_when_trusted(self, trusted_mcp_server):
        res = trusted_mcp_server.update_node("router1", {"host": "10.0.0.254"})
        assert res["success"] is True
        info = trusted_mcp_server.get_node_info("router1")
        assert info["host"] == "10.0.0.254"

    def test_delete_node_blocked_when_untrusted(self, mcp_server):
        res = mcp_server.delete_node("router1")
        assert res["success"] is False
        assert "Permission denied" in res["error"]

    def test_delete_node_allowed_when_trusted(self, trusted_mcp_server):
        res = trusted_mcp_server.delete_node("router1")
        assert res["success"] is True
        nodes = trusted_mcp_server.list_nodes()
        assert "router1" not in nodes


# =============================================================================
# Transport & Logging Redirection Tests
# =============================================================================

class TestTransportAndRedirection:
    def test_stdio_redirects_stdout_and_calls_run(self, mcp_server):
        with patch.object(mcp_server.server, "run") as mock_run, \
             patch("connpy.printer.set_thread_stream") as mock_set_stream:
            mcp_server.run(transport="stdio")
            mock_set_stream.assert_called_once_with(sys.stderr)
            mock_run.assert_called_once_with(transport="stdio")

    def test_sse_passes_host_and_port(self, mcp_server):
        with patch.object(mcp_server.server, "run") as mock_run:
            mcp_server.run(transport="sse", host="0.0.0.0", port=9090)
            mock_run.assert_called_once_with(transport="sse", host="0.0.0.0", port=9090)


# =============================================================================
# Async MCP Protocol Tool Invocation Tests
# =============================================================================

class TestMCPProtocolAsyncCall:
    @pytest.mark.asyncio
    async def test_async_call_tool_list_nodes(self, mcp_server):
        res = await mcp_server.call_tool("list_nodes", {"filter_pattern": "router1"})
        assert not res.is_error
        text = res.content[0].text
        assert "router1" in text

    @pytest.mark.asyncio
    async def test_async_call_tool_get_node_info_masks_password(self, mcp_server):
        res = await mcp_server.call_tool("get_node_info", {"node_name": "router1"})
        assert not res.is_error
        text = res.content[0].text
        assert '"password": "***"' in text
        assert "pass1" not in text


# =============================================================================
# CLI Dispatch & Completion Tests
# =============================================================================

class TestCLIDispatchAndCompletion:
    def test_ai_handler_configure_mcp_serve_dispatch(self, populated_config, services):
        from connpy.cli.ai_handler import AIHandler

        class DummyApp:
            def __init__(self, conf, svcs):
                self.config = conf
                self.services = svcs

        app = DummyApp(populated_config, services)
        handler = AIHandler(app)

        args = MagicMock()
        args.mcp = ["serve", "--sse", "--port", "9191", "--trust"]
        args.trust = False

        with patch("connpy.mcp_server.ConnpyMCPServer.run") as mock_run:
            handler.configure_mcp(args)
            mock_run.assert_called_once_with(
                transport="sse", host="127.0.0.1", port=9191
            )

    def test_completion_contains_serve_flags(self):
        from connpy.completion import _build_tree, resolve_completion
        tree = _build_tree([], [], [], {}, "/tmp")

        # Verify tree hierarchy
        assert "serve" in tree["ai"]["--mcp"]
        serve_sub = tree["ai"]["--mcp"]["serve"]
        assert "--sse" in serve_sub
        assert "--port" in serve_sub
        assert "--trust" in serve_sub

        # Verify completions resolution
        comp_base = resolve_completion(["ai", "--mcp", "serve", ""], tree)
        assert "--sse" in comp_base
        assert "--port" in comp_base
        assert "--trust" in comp_base

        # After --sse, remaining flags are suggested
        comp_after_sse = resolve_completion(["ai", "--mcp", "serve", "--sse", ""], tree)
        assert "--port" in comp_after_sse
        assert "--trust" in comp_after_sse
