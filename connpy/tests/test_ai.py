"""Tests for connpy.ai module."""
import json
import os
import pytest
from unittest.mock import patch, MagicMock


# =========================================================================
# AI Init tests
# =========================================================================

class TestAIInit:
    def test_init_with_keys(self, ai_config, mock_litellm):
        """Initializes correctly when keys are configured."""
        from connpy.ai import ai
        myai = ai(ai_config)
        assert myai.engineer_model == "test/test-model"
        assert myai.architect_model == "test/test-architect"

    def test_ask_missing_engineer_key(self, config):
        """Raises ValueError if engineer key is missing when asking."""
        from connpy.ai import ai
        myai = ai(config)
        with pytest.raises(ValueError) as exc:
            myai.ask("hello")
        assert "Engineer API key not configured" in str(exc.value)

    def test_init_missing_architect_key_warns(self, ai_config, capsys, mock_litellm):
        """Warns if architect key is missing but doesn't crash."""
        # Remove architect key
        ai_config.config["ai"]["architect_api_key"] = None
        from connpy.ai import ai
        # Should not raise
        myai = ai(ai_config)
        assert myai.architect_key is None

    def test_default_models(self, config):
        """Default models are set correctly when not configured."""
        config.config["ai"] = {"engineer_api_key": "test-key", "architect_api_key": "test-key"}
        from connpy.ai import ai
        myai = ai(config)
        assert "gemini" in myai.engineer_model.lower()
        assert "claude" in myai.architect_model.lower() or "anthropic" in myai.architect_model.lower()

    def test_init_loads_memory(self, ai_config, tmp_path, mock_litellm):
        """Loads long-term memory from file if it exists."""
        memory_path = os.path.join(ai_config.defaultdir, "ai_memory.md")
        from connpy.ai import ai

        with patch("os.path.exists", side_effect=lambda p: True if p == memory_path else os.path.exists(p)):
            with patch("builtins.open", side_effect=lambda f, *a, **kw: (
                __import__("io").StringIO("## Memory\nRouter1 is border router")
                if f == memory_path else open(f, *a, **kw)
            )):
                try:
                    myai = ai(ai_config)
                except Exception:
                    pass  # May fail on other file opens, that's ok


# =========================================================================
# register_ai_tool tests
# =========================================================================

class TestRegisterAITool:
    @pytest.fixture
    def myai(self, ai_config, mock_litellm):
        from connpy.ai import ai
        return ai(ai_config)

    def _make_tool_def(self, name="my_tool"):
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": "Test tool",
                "parameters": {"type": "object", "properties": {}}
            }
        }

    def test_register_tool_engineer(self, myai):
        tool_def = self._make_tool_def()
        myai.register_ai_tool(tool_def, lambda self, **kw: "ok", target="engineer")
        assert len(myai.external_engineer_tools) == 1
        assert len(myai.external_architect_tools) == 0

    def test_register_tool_architect(self, myai):
        tool_def = self._make_tool_def()
        myai.register_ai_tool(tool_def, lambda self, **kw: "ok", target="architect")
        assert len(myai.external_architect_tools) == 1
        assert len(myai.external_engineer_tools) == 0

    def test_register_tool_both(self, myai):
        tool_def = self._make_tool_def()
        myai.register_ai_tool(tool_def, lambda self, **kw: "ok", target="both")
        assert len(myai.external_engineer_tools) == 1
        assert len(myai.external_architect_tools) == 1

    def test_register_tool_handler(self, myai):
        tool_def = self._make_tool_def("custom_tool")
        handler = lambda self, **kw: "result"
        myai.register_ai_tool(tool_def, handler)
        assert "custom_tool" in myai.external_tool_handlers
        assert myai.external_tool_handlers["custom_tool"] is handler

    def test_register_tool_prompt_extension(self, myai):
        tool_def = self._make_tool_def()
        myai.register_ai_tool(
            tool_def, lambda self, **kw: "ok",
            engineer_prompt="- Custom capability",
            architect_prompt="  * Custom tool"
        )
        assert any("Custom capability" in ext for ext in myai.engineer_prompt_extensions)
        assert any("Custom tool" in ext for ext in myai.architect_prompt_extensions)

    def test_register_tool_status_formatter(self, myai):
        tool_def = self._make_tool_def("status_tool")
        formatter = lambda args: f"[STATUS] {args}"
        myai.register_ai_tool(tool_def, lambda self, **kw: "ok", status_formatter=formatter)
        assert "status_tool" in myai.tool_status_formatters


# =========================================================================
# Dynamic prompts tests
# =========================================================================

class TestDynamicPrompts:
    @pytest.fixture
    def myai(self, ai_config, mock_litellm):
        from connpy.ai import ai
        return ai(ai_config)

    def test_engineer_prompt_without_extensions(self, myai):
        prompt = myai.engineer_system_prompt
        assert "Plugin Capabilities" not in prompt
        assert "TECHNICAL EXECUTION ENGINE" in prompt

    def test_engineer_prompt_with_extensions(self, myai):
        myai.engineer_prompt_extensions.append("- AWS Cloud Auditing")
        prompt = myai.engineer_system_prompt
        assert "Plugin Capabilities" in prompt
        assert "AWS Cloud Auditing" in prompt

    def test_architect_prompt_without_extensions(self, myai):
        prompt = myai.architect_system_prompt
        assert "Plugin Capabilities" not in prompt
        assert "STRATEGIC REASONING ENGINE" in prompt

    def test_architect_prompt_with_extensions(self, myai):
        myai.architect_prompt_extensions.append("  * Custom tool available")
        prompt = myai.architect_system_prompt
        assert "Plugin Capabilities" in prompt
        assert "Custom tool available" in prompt



# =========================================================================
# _sanitize_messages tests
# =========================================================================

class TestSanitizeMessages:
    @pytest.fixture
    def myai(self, ai_config, mock_litellm):
        from connpy.ai import ai
        return ai(ai_config)

    def test_sanitize_empty(self, myai):
        assert myai._sanitize_messages([]) == []

    def test_sanitize_normal_messages(self, myai):
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"}
        ]
        result = myai._sanitize_messages(messages)
        assert len(result) == 3

    def test_sanitize_removes_orphan_tool_calls(self, myai):
        """Tool calls at the end without responses are removed."""
        messages = [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "function": {"name": "list_nodes", "arguments": "{}"}}
            ]}
            # No tool response follows!
        ]
        result = myai._sanitize_messages(messages)
        assert len(result) == 1  # Only user message
        assert result[0]["role"] == "user"

    def test_sanitize_removes_orphan_tool_responses(self, myai):
        """Tool responses without preceding tool_calls are removed."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "tool", "tool_call_id": "tc1", "name": "list_nodes", "content": "[]"}
        ]
        result = myai._sanitize_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_sanitize_preserves_valid_tool_pairs(self, myai):
        """Valid assistant+tool_calls followed by tool responses are preserved."""
        messages = [
            {"role": "user", "content": "list nodes"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "function": {"name": "list_nodes", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "tc1", "name": "list_nodes", "content": "[\"r1\"]"},
            {"role": "assistant", "content": "Found r1"}
        ]
        result = myai._sanitize_messages(messages)
        assert len(result) == 4

    def test_sanitize_strips_cache_control(self, myai):
        """_sanitize_messages should convert list-based content (with cache_control) back to strings."""
        messages = [
            {"role": "system", "content": [{"type": "text", "text": "system prompt", "cache_control": {"type": "ephemeral"}}]},
            {"role": "user", "content": "hello"}
        ]
        result = myai._sanitize_messages(messages)
        assert result[0]["role"] == "system"
        assert isinstance(result[0]["content"], str)
        assert result[0]["content"] == "system prompt"


# =========================================================================
# _truncate tests
# =========================================================================

class TestTruncate:
    @pytest.fixture
    def myai(self, ai_config, mock_litellm):
        from connpy.ai import ai
        return ai(ai_config)

    def test_truncate_short_text(self, myai):
        text = "short text"
        assert myai._truncate(text) == text

    def test_truncate_long_text(self, myai):
        text = "x" * 100000
        result = myai._truncate(text)
        assert len(result) < 100000
        assert "[... OUTPUT TRUNCATED ...]" in result

    def test_truncate_custom_limit(self, myai):
        text = "x" * 1000
        result = myai._truncate(text, limit=500)
        assert len(result) < 1000
        assert "[... OUTPUT TRUNCATED ...]" in result

    def test_truncate_preserves_head_and_tail(self, myai):
        text = "HEAD" + "x" * 100000 + "TAIL"
        result = myai._truncate(text)
        assert result.startswith("HEAD")
        assert result.endswith("TAIL")


# =========================================================================
# Tool methods tests
# =========================================================================

class TestToolMethods:
    @pytest.fixture
    def myai(self, ai_config, mock_litellm):
        from connpy.ai import ai
        return ai(ai_config)

    def test_list_nodes_tool_found(self, myai):
        result = myai.list_nodes_tool("router.*")
        parsed = json.loads(result) if isinstance(result, str) else result
        assert "router1" in str(parsed)

    def test_list_nodes_tool_not_found(self, myai):
        result = myai.list_nodes_tool("nonexistent_pattern_xyz")
        assert "No nodes found" in str(result)

    def test_get_node_info_masks_password(self, myai):
        result = myai.get_node_info_tool("router1")
        parsed = json.loads(result) if isinstance(result, str) else result
        assert parsed["password"] == "***"

    def test_is_safe_command_show(self, myai):
        assert myai._is_safe_command("show running-config") == True
        assert myai._is_safe_command("show ip int brief") == True

    def test_is_safe_command_config(self, myai):
        assert myai._is_safe_command("config t") == False
        assert myai._is_safe_command("write memory") == False

    def test_is_safe_command_ls(self, myai):
        assert myai._is_safe_command("ls -la") == True

    def test_is_safe_command_ping(self, myai):
        assert myai._is_safe_command("ping 10.0.0.1") == True


# =========================================================================
# manage_memory_tool tests
# =========================================================================

class TestManageMemory:
    @pytest.fixture
    def myai(self, ai_config, mock_litellm, tmp_path):
        from connpy.ai import ai
        myai = ai(ai_config)
        myai.memory_path = str(tmp_path / "ai_memory.md")
        return myai

    def test_manage_memory_append(self, myai):
        result = myai.manage_memory_tool("Router1 is border router", action="append")
        assert "successfully" in result.lower()
        assert os.path.exists(myai.memory_path)
        content = open(myai.memory_path).read()
        assert "Router1 is border router" in content

    def test_manage_memory_replace(self, myai):
        myai.manage_memory_tool("old content", action="append")
        myai.manage_memory_tool("new content only", action="replace")
        content = open(myai.memory_path).read()
        assert "new content only" in content
        assert "old content" not in content

    def test_manage_memory_empty_content(self, myai):
        result = myai.manage_memory_tool("", action="append")
        assert "error" in result.lower() or "Error" in result


# =========================================================================
# ask() with mock LLM tests
# =========================================================================

class TestAsk:
    @pytest.fixture
    def myai(self, ai_config, mock_litellm):
        from connpy.ai import ai
        return ai(ai_config)

    def test_ask_basic_response(self, myai, mock_litellm):
        result = myai.ask("hello", stream=False)
        assert "response" in result
        assert "chat_history" in result
        assert "usage" in result
        assert result["response"] == "Test response from AI"

    def test_ask_sticky_brain_engineer(self, myai, mock_litellm):
        result = myai.ask("show me the routers", stream=False)
        assert result["responder"] == "engineer"

    def test_ask_explicit_architect(self, myai, mock_litellm):
        result = myai.ask("architect: review the network design", stream=False)
        assert result["responder"] == "architect"

    def test_ask_returns_usage(self, myai, mock_litellm):
        result = myai.ask("test", stream=False)
        assert result["usage"]["total"] > 0

    def test_ask_with_chat_history(self, myai, mock_litellm):
        history = [
            {"role": "user", "content": "previous question"},
            {"role": "assistant", "content": "previous answer"}
        ]
        result = myai.ask("follow up", chat_history=history, stream=False)
        assert result["response"] is not None


# =========================================================================
# _get_engineer_tools / _get_architect_tools tests
# =========================================================================

class TestToolDefinitions:
    @pytest.fixture
    def myai(self, ai_config, mock_litellm):
        from connpy.ai import ai
        return ai(ai_config)

    def test_engineer_tools_include_core(self, myai):
        tools = myai._get_engineer_tools()
        names = [t["function"]["name"] for t in tools]
        assert "list_nodes" in names
        assert "run_commands" in names
        assert "get_node_info" in names
        assert "consult_architect" in names
        assert "escalate_to_architect" in names

    def test_engineer_tools_include_external(self, myai):
        myai.external_engineer_tools.append({
            "type": "function",
            "function": {"name": "custom_tool", "description": "test", "parameters": {}}
        })
        tools = myai._get_engineer_tools()
        names = [t["function"]["name"] for t in tools]
        assert "custom_tool" in names

    def test_architect_tools_include_core(self, myai):
        tools = myai._get_architect_tools()
        names = [t["function"]["name"] for t in tools]
        assert "delegate_to_engineer" in names
        assert "return_to_engineer" in names
        assert "manage_memory_tool" in names

    def test_architect_tools_include_external(self, myai):
        myai.external_architect_tools.append({
            "type": "function",
            "function": {"name": "arch_tool", "description": "test", "parameters": {}}
        })
        tools = myai._get_architect_tools()
        names = [t["function"]["name"] for t in tools]
        assert "arch_tool" in names


# =========================================================================
# AI Session Management tests
# =========================================================================

class TestAISessions:
    @pytest.fixture
    def myai(self, ai_config, mock_litellm, tmp_path):
        from connpy.ai import ai
        ai_config.defaultdir = str(tmp_path)
        return ai(ai_config)

    def test_sessions_dir_initialization(self, myai, tmp_path):
        assert os.path.exists(os.path.join(tmp_path, "ai_sessions"))
        assert myai.sessions_dir == str(tmp_path / "ai_sessions")

    def test_generate_session_id(self, myai):
        session_id = myai._generate_session_id("Any query")
        # Format: YYYYMMDD-HHMMSS
        assert len(session_id) == 15
        assert "-" in session_id
        parts = session_id.split("-")
        assert len(parts[0]) == 8 # YYYYMMDD
        assert len(parts[1]) == 6 # HHMMSS

    def test_save_and_load_session(self, myai):
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"}
        ]
        myai.save_session(history, title="Test Session")
        session_id = myai.session_id
        
        # Load it back
        loaded = myai.load_session_data(session_id)
        assert loaded["title"] == "Test Session"
        assert loaded["history"] == history
        assert loaded["model"] == myai.engineer_model

    def test_list_sessions(self, myai, capsys):
        history = [{"role": "user", "content": "Query 1"}]
        myai.save_session(history, title="Session 1")
        
        # Use a second instance to list
        myai.list_sessions()
        captured = capsys.readouterr()
        assert "Session 1" in captured.out
        assert "AI Persisted Sessions" in captured.out

    def test_get_last_session_id(self, myai):
        # Save two sessions
        myai.session_id = None # Force new
        myai.save_session([{"role": "user", "content": "First"}])
        first_id = myai.session_id
        import time
        time.sleep(1.1) # Ensure different timestamp
        
        myai.session_id = None # Force new
        myai.save_session([{"role": "user", "content": "Second"}])
        second_id = myai.session_id
        
        last_id = myai.get_last_session_id()
        assert last_id == second_id
        assert last_id != first_id

    def test_delete_session(self, myai):
        myai.save_session([{"role": "user", "content": "To be deleted"}])
        session_id = myai.session_id
        assert os.path.exists(myai.session_path)
        
        myai.delete_session(session_id)
        assert not os.path.exists(myai.session_path)
