import pytest
import json
from unittest.mock import patch, MagicMock
from connpy.ai import PlaybookBuilderAgent
from connpy.services.ai_service import AIService

# =========================================================================
# PlaybookBuilderAgent validation tests
# =========================================================================

def test_validate_playbook_valid(ai_config):
    """Verifies that a valid canonical tasks[] playbook passes validation."""
    agent = PlaybookBuilderAgent(ai_config)
    
    valid_yaml = """
    tasks:
    - name: "Apply standard config"
      action: "run"
      nodes: "router1"
      commands:
      - "conf t"
      - "end"
      output: "stdout"
    - name: "Verify connectivity"
      action: "test"
      nodes: "router1"
      commands:
      - "ping 10.0.0.1"
      expected: "!"
      output: "stdout"
    """
    
    res = agent.validate_playbook(valid_yaml)
    assert res["valid"] is True
    assert "valid" in res["message"].lower()

def test_validate_playbook_invalid_yaml(ai_config):
    """Verifies that syntax errors in YAML are caught and reported."""
    agent = PlaybookBuilderAgent(ai_config)
    
    invalid_yaml = """
    tasks:
    - name: "Broken task"
      action: "run
      nodes: "router1"
    """
    
    res = agent.validate_playbook(invalid_yaml)
    assert res["valid"] is False
    assert "syntax error" in res["error"].lower()

def test_validate_playbook_missing_tasks_key(ai_config):
    """Verifies that a playbook without tasks root key is invalid."""
    agent = PlaybookBuilderAgent(ai_config)
    
    invalid_yaml = """
    not_tasks:
    - name: "Apply standard config"
      action: "run"
      nodes: "router1"
      commands:
      - "conf t"
      output: "stdout"
    """
    
    res = agent.validate_playbook(invalid_yaml)
    assert res["valid"] is False
    assert "missing mandatory root 'tasks' key" in res["error"].lower()

def test_validate_playbook_missing_mandatory_fields(ai_config):
    """Verifies that missing name, action, nodes, commands, or output triggers a validation failure."""
    agent = PlaybookBuilderAgent(ai_config)
    
    # Missing nodes
    invalid_yaml = """
    tasks:
    - name: "Apply standard config"
      action: "run"
      commands:
      - "conf t"
      output: "stdout"
    """
    res = agent.validate_playbook(invalid_yaml)
    assert res["valid"] is False
    assert "missing mandatory fields" in res["error"].lower()
    assert "nodes" in res["error"]

def test_validate_playbook_invalid_action(ai_config):
    """Verifies that an unsupported action type is caught."""
    agent = PlaybookBuilderAgent(ai_config)
    
    invalid_yaml = """
    tasks:
    - name: "Apply standard config"
      action: "delete_everything"
      nodes: "router1"
      commands:
      - "conf t"
      output: "stdout"
    """
    res = agent.validate_playbook(invalid_yaml)
    assert res["valid"] is False
    assert "invalid action" in res["error"].lower()

def test_validate_playbook_missing_expected_in_test(ai_config):
    """Verifies that action 'test' requires the expected field."""
    agent = PlaybookBuilderAgent(ai_config)
    
    invalid_yaml = """
    tasks:
    - name: "Apply standard config"
      action: "test"
      nodes: "router1"
      commands:
      - "ping 10.0.0.1"
      output: "stdout"
    """
    res = agent.validate_playbook(invalid_yaml)
    assert res["valid"] is False
    assert "missing the mandatory 'expected' key" in res["error"].lower()

def test_validate_playbook_invalid_nodes_type(ai_config):
    """Verifies that nodes of invalid type (e.g. integer) is caught."""
    agent = PlaybookBuilderAgent(ai_config)
    
    invalid_yaml = """
    tasks:
    - name: "Apply config"
      action: "run"
      nodes: 12345
      commands:
      - "conf t"
      output: "stdout"
    """
    res = agent.validate_playbook(invalid_yaml)
    assert res["valid"] is False
    assert "nodes' must be a string (regex) or a list of strings (regexes)" in res["error"]

def test_validate_playbook_invalid_nodes_list_item(ai_config):
    """Verifies that nodes list containing non-string items is caught."""
    agent = PlaybookBuilderAgent(ai_config)
    
    invalid_yaml = """
    tasks:
    - name: "Apply config"
      action: "run"
      nodes:
      - "router1"
      - 9999
      commands:
      - "conf t"
      output: "stdout"
    """
    res = agent.validate_playbook(invalid_yaml)
    assert res["valid"] is False
    assert "list contains a non-string value" in res["error"]


# =========================================================================
# AIService new methods delegation tests
# =========================================================================

def test_build_playbook_chat_delegation(ai_config):
    """Verifies that build_playbook_chat instantiates PlaybookBuilderAgent and delegates ask."""
    service = AIService(ai_config)
    
    with patch("connpy.ai.PlaybookBuilderAgent") as MockAgentClass:
        mock_agent = MockAgentClass.return_value
        mock_agent.ask.return_value = {"response": "Mock response", "chat_history": []}
        
        history = [{"role": "user", "content": "build playbook"}]
        res = service.build_playbook_chat("help me", chat_history=history)
        
        MockAgentClass.assert_called_once_with(ai_config)
        mock_agent.ask.assert_called_once_with("help me", chat_history=history, status=None, chunk_callback=None)
        assert res["response"] == "Mock response"

def test_analyze_execution_results_delegation(ai_config):
    """Verifies that analyze_execution_results formats prompt with @architect and delegates to self.ask."""
    service = AIService(ai_config)
    service.ask = MagicMock()
    
    results = {"router1": {"output": "success", "status": 0}}
    service.analyze_execution_results(results, query="diagnose border")
    
    service.ask.assert_called_once()
    args, kwargs = service.ask.call_args
    prompt = args[0]
    
    assert prompt.startswith("@architect:")
    assert "diagnose border" in prompt
    assert "Results Data:" in prompt
    assert "router1" in prompt
    assert kwargs.get("one_shot") is True

def test_predict_execution_results_delegation(ai_config):
    """Verifies that predict_execution_results formats prompt with @engineer and delegates to self.ask."""
    service = AIService(ai_config)
    service.ask = MagicMock()
    
    nodes = ["router1", "router2"]
    commands = ["conf t", "interface lo0"]
    service.predict_execution_results(nodes, commands)
    
    service.ask.assert_called_once()
    args, kwargs = service.ask.call_args
    prompt = args[0]
    
    assert prompt.startswith("@engineer:")
    assert "Preflight Simulation Agent" in prompt
    assert "router1, router2" in prompt
    assert "conf t" in prompt
    assert "interface lo0" in prompt


# =========================================================================
# gRPC Integration Tests for AIService
# =========================================================================

import grpc
from concurrent import futures
from connpy.grpc_layer import server, connpy_pb2, connpy_pb2_grpc, stubs

class TestGRPCAIIntegration:
    @pytest.fixture
    def grpc_server(self, populated_config):
        """Starts a local gRPC server for IA integration testing."""
        srv = grpc.server(futures.ThreadPoolExecutor(max_workers=5))
        connpy_pb2_grpc.add_AIServiceServicer_to_server(server.ServerServicer(populated_config).ai if hasattr(server, 'ServerServicer') else server.AIServicer(populated_config), srv)
        port = srv.add_insecure_port('127.0.0.1:0')
        srv.start()
        yield f"127.0.0.1:{port}"
        srv.stop(0)

    @pytest.fixture
    def channel(self, grpc_server):
        with grpc.insecure_channel(grpc_server) as channel:
            yield channel

    @pytest.fixture
    def ai_stub(self, channel):
        return stubs.AIStub(channel, "localhost")

    def test_build_playbook_chat_grpc(self, ai_stub, populated_config):
        """Verifies that build_playbook_chat gRPC stream functions correctly."""
        # Mock PlaybookBuilderAgent.ask to simulate agent response stream
        def mock_ask(user_input, chat_history=None, status=None, debug=False, chunk_callback=None):
            if chunk_callback:
                chunk_callback("Generated Tasks:\n- name: config")
            return {"response": "Done", "playbook_yaml": "tasks:\n- name: config"}

        with patch("connpy.ai.PlaybookBuilderAgent.ask", side_effect=mock_ask):
            chunks = []
            def callback(chunk):
                chunks.append(chunk)

            res = ai_stub.build_playbook_chat("make playbook", chunk_callback=callback)
            assert "tasks:" in res["playbook_yaml"]
            assert len(chunks) > 0
            assert "Generated Tasks:" in chunks[0]

    def test_analyze_execution_results_grpc(self, ai_stub, populated_config):
        """Verifies that analyze_execution_results gRPC stream functions correctly."""
        # Mock AIService.ask to simulate response stream
        def mock_ask(prompt, status=None, debug=False, chunk_callback=None, **kwargs):
            if chunk_callback:
                chunk_callback("Results are optimal.")
            return {"response": "Done"}

        with patch.object(AIService, "ask", side_effect=mock_ask):
            chunks = []
            def callback(chunk):
                chunks.append(chunk)

            res = ai_stub.analyze_execution_results({"r1": "ok"}, query="test query", chunk_callback=callback)
            assert res is not None
            assert len(chunks) > 0
            assert "optimal" in chunks[0]

    def test_predict_execution_results_grpc(self, ai_stub, populated_config):
        """Verifies that predict_execution_results gRPC stream functions correctly."""
        # Mock AIService.ask to simulate response stream
        def mock_ask(prompt, status=None, debug=False, chunk_callback=None, **kwargs):
            if chunk_callback:
                chunk_callback("Commands are safe.")
            return {"response": "Done"}

        with patch.object(AIService, "ask", side_effect=mock_ask):
            chunks = []
            def callback(chunk):
                chunks.append(chunk)

            res = ai_stub.predict_execution_results(["r1"], ["show version"], chunk_callback=callback)
            assert res is not None
            assert len(chunks) > 0
            assert "safe" in chunks[0]
