"""Unit tests for AI bugfixes, MethodHook instance isolation, and MCP multi-tenancy.

Covering specification: spec-02-ai-and-hooks-bugfixes.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from connpy.ai import PlaybookBuilderAgent, ai
from connpy.hooks import MethodHook
from connpy.mcp_client import MCPClientManager

# =========================================================================
# 1. AI Long-Term Memory Bugfix Tests
# =========================================================================

class TestAILongTermMemory:
    """Tests ensuring long_term_memory interpolation and updates work correctly."""

    def test_long_term_memory_injected_without_template_literal(self, ai_config, mock_litellm):
        """Verify no double braces or unresolved '{self.long_term_memory' literals exist."""
        myai = ai(ai_config)
        assert "{self.long_term_memory" not in myai.engineer_system_prompt
        assert "{{self.long_term_memory" not in myai.engineer_system_prompt
        assert "{self.long_term_memory" not in myai.architect_system_prompt
        assert "Network Context: Empty." in myai.engineer_system_prompt
        assert "Network Context: Empty." in myai.architect_system_prompt

    def test_manage_memory_tool_updates_prompts_immediately(self, ai_config, mock_litellm):
        """Verify calling manage_memory_tool immediately updates engineer and architect prompts."""
        myai = ai(ai_config)
        res = myai.manage_memory_tool("DC-East ASN is 65010", action="append")
        assert "Memory updated successfully" in res
        assert "DC-East ASN is 65010" in myai.long_term_memory
        assert "DC-East ASN is 65010" in myai.engineer_system_prompt
        assert "DC-East ASN is 65010" in myai.architect_system_prompt
        assert "{self.long_term_memory" not in myai.engineer_system_prompt

    def test_init_with_preexisting_memory_file(self, ai_config, mock_litellm):
        """Verify preexisting ai_memory.md content is loaded into prompts on startup."""
        mem_file = os.path.join(ai_config.defaultdir, "ai_memory.md")
        with open(mem_file, "w") as f:
            f.write("Border routers running BGP EVPN")

        myai = ai(ai_config)
        assert "Border routers running BGP EVPN" in myai.long_term_memory
        assert "Border routers running BGP EVPN" in myai.engineer_system_prompt
        assert "Border routers running BGP EVPN" in myai.architect_system_prompt


# =========================================================================
# 2. AI has_architect and architect_auth Tests
# =========================================================================

class TestAIArchitectAuthAndTools:
    """Tests ensuring architect tools and authentication function with architect_auth."""

    def test_engineer_tools_include_architect_when_auth_configured_without_key(self, ai_config, mock_litellm):
        """consult_architect & escalate_to_architect must be available with architect_auth even without key."""
        ai_config.config["ai"]["architect_api_key"] = None
        ai_config.config["ai"]["architect_auth"] = {"service_account": "sa@google.com"}

        myai = ai(ai_config)
        assert myai.architect_key is None
        assert myai.has_architect is True

        tools = myai._get_engineer_tools()
        tool_names = [t["function"]["name"] for t in tools]
        assert "consult_architect" in tool_names
        assert "escalate_to_architect" in tool_names

    def test_engineer_tools_include_architect_for_keyless_model(self, ai_config, mock_litellm):
        """Vertex AI / Ollama keyless models must have architect tools enabled."""
        ai_config.config["ai"]["architect_api_key"] = None
        ai_config.config["ai"]["architect_auth"] = {}
        ai_config.config["ai"]["architect_model"] = "vertex_ai/gemini-1.5-pro"

        myai = ai(ai_config)
        assert myai.has_architect is True
        tools = myai._get_engineer_tools()
        tool_names = [t["function"]["name"] for t in tools]
        assert "consult_architect" in tool_names
        assert "escalate_to_architect" in tool_names

    def test_consult_architect_passes_auth_kwargs_cleanly(self, ai_config):
        """consult_architect must pass dict(architect_auth) to litellm.completion."""
        ai_config.config["ai"]["architect_api_key"] = None
        ai_config.config["ai"]["architect_auth"] = {
            "api_base": "https://custom-ai.internal/v1",
            "custom_header": "test-val",
        }

        myai = ai(ai_config)

        with patch("connpy.ai.completion") as mock_comp:
            fake_resp = MagicMock()
            fake_msg = MagicMock()
            fake_msg.content = "Architect advice on spine-leaf"
            fake_msg.tool_calls = None
            fake_msg.role = "assistant"
            fake_msg.model_dump.return_value = {"role": "assistant", "content": "Advice"}
            fake_choice = MagicMock()
            fake_choice.message = fake_msg
            fake_resp.choices = [fake_choice]
            fake_resp.usage = MagicMock()
            fake_resp.usage.prompt_tokens = 10
            fake_resp.usage.completion_tokens = 10
            fake_resp.usage.total_tokens = 20

            # Craft first completion call that instructs engineer to consult architect
            tc = MagicMock()
            tc.function.name = "consult_architect"
            tc.function.arguments = '{"question": "How to design MTU?", "technical_summary": "9000 vs 1500"}'
            eng_msg = MagicMock()
            eng_msg.role = "assistant"
            eng_msg.content = ""
            eng_msg.tool_calls = [tc]
            eng_msg.model_dump.return_value = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "consult_architect",
                        "arguments": '{"question": "How to design MTU?", "technical_summary": "9000 vs 1500"}',
                    },
                }],
            }
            eng_choice = MagicMock()
            eng_choice.message = eng_msg
            eng_resp = MagicMock()
            eng_resp.choices = [eng_choice]
            eng_resp.usage = MagicMock()
            eng_resp.usage.prompt_tokens = 5
            eng_resp.usage.completion_tokens = 5
            eng_resp.usage.total_tokens = 10

            # Final response after consultation
            final_msg = MagicMock()
            final_msg.role = "assistant"
            final_msg.content = "Here is the guidance."
            final_msg.tool_calls = None
            final_msg.model_dump.return_value = {"role": "assistant", "content": "Here is the guidance."}
            final_choice = MagicMock()
            final_choice.message = final_msg
            final_resp = MagicMock()
            final_resp.choices = [final_choice]
            final_resp.usage = MagicMock()
            final_resp.usage.prompt_tokens = 5
            final_resp.usage.completion_tokens = 5
            final_resp.usage.total_tokens = 10

            mock_comp.side_effect = [eng_resp, fake_resp, final_resp]

            res = myai.ask("Design MTU question", stream=False)
            assert "guidance" in res["response"]

            # Second call to completion was consult_architect
            assert mock_comp.call_count >= 2
            consult_call = mock_comp.call_args_list[1]
            kwargs = consult_call.kwargs
            assert kwargs.get("api_base") == "https://custom-ai.internal/v1"
            assert kwargs.get("custom_header") == "test-val"
            assert "api_key" not in kwargs

    def test_consult_architect_injects_architect_key_if_missing_from_auth(self, ai_config):
        """If architect_key exists and 'api_key' is not in architect_auth, it must be added."""
        ai_config.config["ai"]["architect_api_key"] = "arch-key-12345"
        ai_config.config["ai"]["architect_auth"] = {"api_base": "https://custom-ai.internal/v1"}

        myai = ai(ai_config)

        with patch("connpy.ai.completion") as mock_comp:
            fake_resp = MagicMock()
            fake_msg = MagicMock()
            fake_msg.content = "Advice"
            fake_msg.tool_calls = None
            fake_msg.role = "assistant"
            fake_msg.model_dump.return_value = {"role": "assistant", "content": "Advice"}
            fake_choice = MagicMock()
            fake_choice.message = fake_msg
            fake_resp.choices = [fake_choice]
            fake_resp.usage = MagicMock()
            fake_resp.usage.prompt_tokens = 10
            fake_resp.usage.completion_tokens = 10
            fake_resp.usage.total_tokens = 20

            tc = MagicMock()
            tc.function.name = "consult_architect"
            tc.function.arguments = '{"question": "Q?", "technical_summary": "S"}'
            eng_msg = MagicMock()
            eng_msg.role = "assistant"
            eng_msg.content = ""
            eng_msg.tool_calls = [tc]
            eng_msg.model_dump.return_value = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "consult_architect",
                        "arguments": '{"question": "Q?", "technical_summary": "S"}',
                    },
                }],
            }
            eng_choice = MagicMock()
            eng_choice.message = eng_msg
            eng_resp = MagicMock()
            eng_resp.choices = [eng_choice]
            eng_resp.usage = MagicMock()
            eng_resp.usage.prompt_tokens = 5
            eng_resp.usage.completion_tokens = 5
            eng_resp.usage.total_tokens = 10

            final_msg = MagicMock()
            final_msg.role = "assistant"
            final_msg.content = "Done."
            final_msg.tool_calls = None
            final_msg.model_dump.return_value = {"role": "assistant", "content": "Done."}
            final_choice = MagicMock()
            final_choice.message = final_msg
            final_resp = MagicMock()
            final_resp.choices = [final_choice]
            final_resp.usage = MagicMock()
            final_resp.usage.prompt_tokens = 5
            final_resp.usage.completion_tokens = 5
            final_resp.usage.total_tokens = 10

            mock_comp.side_effect = [eng_resp, fake_resp, final_resp]

            myai.ask("Consult question", stream=False)
            consult_call = mock_comp.call_args_list[1]
            kwargs = consult_call.kwargs
            assert kwargs.get("api_key") == "arch-key-12345"
            assert kwargs.get("api_base") == "https://custom-ai.internal/v1"


# =========================================================================
# 3. PlaybookBuilderAgent Safe YAML Validation Tests
# =========================================================================

class TestPlaybookValidationSafeLoad:
    """Tests verifying PlaybookBuilderAgent.validate_playbook uses safe_load."""

    def test_validate_playbook_rejects_malicious_yaml_tags(self, config):
        """Ensure arbitrary Python object deserialization payloads are safely rejected."""
        agent = PlaybookBuilderAgent(config)
        malicious_yaml = """
        tasks:
          - name: Exploit
            command: !!python/object/apply:os.system ["touch /tmp/hacked_marker"]
        """
        res = agent.validate_playbook(malicious_yaml)
        assert res["valid"] is False
        assert "YAML Syntax Error" in res["error"]
        assert not os.path.exists("/tmp/hacked_marker")

    def test_validate_playbook_accepts_valid_yaml(self, config):
        """Ensure safe YAML with tasks structure validates successfully."""
        agent = PlaybookBuilderAgent(config)
        valid_yaml = """
        tasks:
          - name: Check BGP
            action: run
            nodes: "router.*"
            commands:
              - show ip bgp summary
            output: stdout
        """
        res = agent.validate_playbook(valid_yaml)
        assert res["valid"] is True

    def test_validate_playbook_invalid_structures(self, config):
        """Ensure non-dict or missing 'tasks' YAML is rejected cleanly."""
        agent = PlaybookBuilderAgent(config)
        assert agent.validate_playbook("- not a dict\n- just a list")["valid"] is False
        assert agent.validate_playbook("simple_scalar_string")["valid"] is False
        assert agent.validate_playbook("other_root: 123")["valid"] is False
        assert agent.validate_playbook("tasks: 'not a list'")["valid"] is False


# =========================================================================
# 4. MethodHook Instance Isolation and Deduplication Tests
# =========================================================================

class TestMethodHookIsolation:
    """Tests ensuring MethodHook instances are strictly isolated and deduplicated."""

    def test_instance_hooks_do_not_contaminate_other_instances(self):
        """Registering a hook on inst1 must not run on inst2 or modify class hooks."""
        class NetworkService:
            @MethodHook
            def run(self, cmd):
                return f"executed:{cmd}"

        inst1 = NetworkService()
        inst2 = NetworkService()

        def inst1_post_hook(*args, **kwargs):
            return kwargs["result"] + " [audited-by-1]"

        inst1.run.register_post_hook(inst1_post_hook)

        assert inst1.run("show clock") == "executed:show clock [audited-by-1]"
        assert inst2.run("show clock") == "executed:show clock"
        assert NetworkService.run.post_hooks == []

    def test_instance_hooks_deduplication(self):
        """Registering the same callable multiple times on an instance runs only once."""
        class NetworkService:
            @MethodHook
            def run(self, cmd):
                return f"res:{cmd}"

        calls = []

        def counting_hook(*args, **kwargs):
            calls.append("called")
            return kwargs["result"]

        inst = NetworkService()
        inst.run.register_post_hook(counting_hook)
        inst.run.register_post_hook(counting_hook)
        inst.run.register_post_hook(counting_hook)

        res = inst.run("test")
        assert res == "res:test"
        assert calls == ["called"]

    def test_class_level_and_instance_level_execution_order(self):
        """Class pre-hooks run before instance pre-hooks; class post-hooks run before instance post-hooks."""
        class ExecutionDevice:
            @MethodHook
            def execute(self, val):
                trace.append(f"base:{val}")
                return val

        trace = []

        def class_pre(self, val):
            trace.append("class_pre")
            return (self, val), {}

        def inst_pre(self, val):
            trace.append("inst_pre")
            return (self, val), {}

        def class_post(*args, **kwargs):
            trace.append("class_post")
            return kwargs["result"]

        def inst_post(*args, **kwargs):
            trace.append("inst_post")
            return kwargs["result"]

        ExecutionDevice.execute.register_pre_hook(class_pre)
        ExecutionDevice.execute.register_post_hook(class_post)

        dev = ExecutionDevice()
        dev.execute.register_pre_hook(inst_pre)
        dev.execute.register_post_hook(inst_post)

        dev.execute("ping")
        assert trace == ["class_pre", "inst_pre", "base:ping", "class_post", "inst_post"]

    def test_configfile_instance_isolation(self, config):
        """Verify two configfile instances do not leak hooks across instances."""
        from connpy.configfile import configfile
        conf2 = configfile(conf=config.file, key=config.key)

        captured = []

        def custom_hook(*args, **kwargs):
            captured.append("c1_hook")
            return kwargs["result"]

        config._getallnodes.register_post_hook(custom_hook)

        config._getallnodes(".*")
        assert captured == ["c1_hook"]

        # Call on second instance — hook must NOT execute
        captured.clear()
        conf2._getallnodes(".*")
        assert captured == []


# =========================================================================
# 5. MCPClientManager Multi-Tenancy and Invalidation Tests
# =========================================================================

class DummyConfig:
    """Mock config object with config attribute and defaultdir."""

    def __init__(self, defaultdir: str, mcp_servers: dict | None = None):
        self.defaultdir = defaultdir
        self.config = {
            "ai": {
                "mcp_servers": mcp_servers or {},
            },
        }


class TestMCPClientManagerMultiTenancy:
    """Tests ensuring MCPClientManager isolates tenants and invalidates cache properly."""

    @pytest.fixture(autouse=True)
    def reset_mcp_singletons(self):
        """Clean MCPClientManager instances before and after each test."""
        MCPClientManager._instances.clear()
        MCPClientManager._instance = None
        yield
        MCPClientManager._instances.clear()
        MCPClientManager._instance = None

    def test_mcp_manager_scoped_by_defaultdir(self):
        """Different defaultdir paths produce distinct isolated instances."""
        cfg_tenant1 = DummyConfig("/var/connpy/tenant1")
        cfg_tenant2 = DummyConfig("/var/connpy/tenant2")

        mgr1 = MCPClientManager(cfg_tenant1)
        mgr2 = MCPClientManager(cfg_tenant2)

        assert mgr1 is not mgr2
        assert mgr1.config is cfg_tenant1
        assert mgr2.config is cfg_tenant2

    def test_mcp_manager_same_scope_returns_same_instance_and_updates_config(self):
        """Same defaultdir reuses manager and updates self.config."""
        cfg_a = DummyConfig("/var/connpy/shared")
        cfg_b = DummyConfig("/var/connpy/shared")

        mgr_a = MCPClientManager(cfg_a)
        mgr_b = MCPClientManager(cfg_b)

        assert mgr_a is mgr_b
        assert mgr_a.config is cfg_b

    def test_mcp_manager_scoped_by_id_when_no_defaultdir(self):
        """Configs without defaultdir are isolated by id(config)."""
        cfg1 = object()
        cfg2 = object()

        mgr1 = MCPClientManager(cfg1)
        mgr2 = MCPClientManager(cfg2)

        assert mgr1 is not mgr2

    @pytest.mark.asyncio
    async def test_get_tools_invalidates_cache_on_config_change(self):
        """When server config changes in mcp_servers, tool_cache is invalidated."""
        cfg = DummyConfig(
            "/tmp/test_mcp_invalidation",
            mcp_servers={"srv1": {"url": "http://127.0.0.1:8001/sse", "enabled": True}},
        )

        mgr = MCPClientManager(cfg)
        mgr.tool_cache["srv1"] = [{"type": "function", "function": {"name": "old_tool"}}]
        mock_stack = AsyncMock()
        mgr.sessions["srv1"] = {"session": MagicMock(), "stack": mock_stack}
        mgr._server_configs["srv1"] = {"url": "http://127.0.0.1:8001/sse", "enabled": True}

        # Modify config to point to new URL
        cfg.config["ai"]["mcp_servers"]["srv1"] = {"url": "http://127.0.0.1:8002/sse", "enabled": True}

        new_session = MagicMock()
        with patch.object(mgr, "_ensure_connected", new_callable=AsyncMock) as mock_conn, \
             patch.object(mgr, "_fetch_tools_as_openai", new_callable=AsyncMock) as mock_fetch:
            mock_conn.return_value = new_session
            mock_fetch.return_value = [{"type": "function", "function": {"name": "new_tool"}}]

            tools = await mgr.get_tools_for_llm()

            # Ensure old stack was closed
            mock_stack.aclose.assert_awaited_once()
            # Ensure new tools returned
            assert tools == [{"type": "function", "function": {"name": "new_tool"}}]
            assert mgr.tool_cache["srv1"] == [{"type": "function", "function": {"name": "new_tool"}}]

        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_cleans_resources_and_resets(self):
        """shutdown() closes all stacks, clears caches and removes from _instances."""
        cfg = DummyConfig("/tmp/test_mcp_shutdown")

        mgr = MCPClientManager(cfg)
        mock_stack = AsyncMock()
        mgr.sessions["test"] = {"session": MagicMock(), "stack": mock_stack}
        mgr.tool_cache["test"] = [{"dummy": 1}]

        await mgr.shutdown()

        mock_stack.aclose.assert_awaited_once()
        assert mgr.sessions == {}
        assert mgr.tool_cache == {}
        assert os.path.abspath(cfg.defaultdir) not in MCPClientManager._instances
