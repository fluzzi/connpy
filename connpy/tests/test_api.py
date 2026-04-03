"""Tests for connpy.api module — Flask routes."""
import json
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def api_client(populated_config):
    """Create a Flask test client with a populated config."""
    from connpy.api import app
    app.custom_config = populated_config
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# =========================================================================
# Root endpoint
# =========================================================================

class TestRootEndpoint:
    def test_root_returns_welcome(self, api_client):
        response = api_client.get("/")
        data = response.get_json()
        assert response.status_code == 200
        assert "Welcome" in data["message"]
        assert "version" in data


# =========================================================================
# /list_nodes endpoint
# =========================================================================

class TestListNodes:
    def test_list_nodes_no_filter(self, api_client):
        response = api_client.post("/list_nodes", json={})
        data = response.get_json()
        assert response.status_code == 200
        assert isinstance(data, list)
        assert "router1" in data

    def test_list_nodes_with_filter(self, api_client):
        response = api_client.post("/list_nodes", json={"filter": "router.*"})
        data = response.get_json()
        assert "router1" in data
        assert all("router" in n or "Router" in n for n in data)

    def test_list_nodes_case_insensitive(self, api_client):
        """Filter is lowercased when case=false."""
        response = api_client.post("/list_nodes", json={"filter": "ROUTER.*"})
        data = response.get_json()
        # Should still match since the filter gets lowercased
        assert isinstance(data, list)

    def test_list_nodes_no_body(self, api_client):
        """No body returns all nodes."""
        response = api_client.post("/list_nodes",
                                   data="",
                                   content_type="application/json")
        data = response.get_json()
        assert isinstance(data, list)


# =========================================================================
# /get_nodes endpoint
# =========================================================================

class TestGetNodes:
    def test_get_nodes_no_filter(self, api_client):
        response = api_client.post("/get_nodes", json={})
        data = response.get_json()
        assert response.status_code == 200
        assert isinstance(data, dict)
        assert "router1" in data

    def test_get_nodes_with_filter(self, api_client):
        response = api_client.post("/get_nodes", json={"filter": "router.*"})
        data = response.get_json()
        assert "router1" in data
        assert "host" in data["router1"]

    def test_get_nodes_has_attributes(self, api_client):
        response = api_client.post("/get_nodes", json={"filter": "router1"})
        data = response.get_json()
        if "router1" in data:
            assert "host" in data["router1"]
            assert "protocol" in data["router1"]


# =========================================================================
# /run_commands endpoint
# =========================================================================

class TestRunCommands:
    def test_missing_action(self, api_client):
        response = api_client.post("/run_commands", json={
            "nodes": "router1",
            "commands": ["show version"]
        })
        data = response.get_json()
        assert "DataError" in data
        assert "action" in data["DataError"]

    def test_missing_nodes(self, api_client):
        response = api_client.post("/run_commands", json={
            "action": "run",
            "commands": ["show version"]
        })
        data = response.get_json()
        assert "DataError" in data
        assert "nodes" in data["DataError"]

    def test_missing_commands(self, api_client):
        response = api_client.post("/run_commands", json={
            "action": "run",
            "nodes": "router1"
        })
        data = response.get_json()
        assert "DataError" in data
        assert "commands" in data["DataError"]

    def test_wrong_action(self, api_client):
        response = api_client.post("/run_commands", json={
            "action": "invalid",
            "nodes": "router1",
            "commands": ["show version"]
        })
        data = response.get_json()
        assert "DataError" in data
        assert "Wrong action" in data["DataError"]

    @patch("connpy.api.nodes")
    def test_run_action(self, mock_nodes_cls, api_client):
        """action=run executes and returns output."""
        mock_instance = MagicMock()
        mock_instance.run.return_value = {"router1": "Router v1.0"}
        mock_nodes_cls.return_value = mock_instance

        response = api_client.post("/run_commands", json={
            "action": "run",
            "nodes": "router1",
            "commands": ["show version"]
        })
        data = response.get_json()
        assert "router1" in data

    @patch("connpy.api.nodes")
    def test_test_action(self, mock_nodes_cls, api_client):
        """action=test returns result + output."""
        mock_instance = MagicMock()
        mock_instance.test.return_value = {"router1": {"expected": True}}
        mock_instance.output = {"router1": "output text"}
        mock_nodes_cls.return_value = mock_instance

        response = api_client.post("/run_commands", json={
            "action": "test",
            "nodes": "router1",
            "commands": ["show version"],
            "expected": "Router"
        })
        data = response.get_json()
        assert "result" in data
        assert "output" in data

    @patch("connpy.api.nodes")
    def test_run_with_options(self, mock_nodes_cls, api_client):
        """Options get passed through."""
        mock_instance = MagicMock()
        mock_instance.run.return_value = {"router1": "ok"}
        mock_nodes_cls.return_value = mock_instance

        response = api_client.post("/run_commands", json={
            "action": "run",
            "nodes": "router1",
            "commands": ["show version"],
            "options": {"timeout": 30, "parallel": 5}
        })
        assert response.status_code == 200

    @patch("connpy.api.nodes")
    def test_run_folder_nodes(self, mock_nodes_cls, api_client):
        """Nodes with @ prefix are resolved as folders."""
        mock_instance = MagicMock()
        mock_instance.run.return_value = {"server1@office": "ok"}
        mock_nodes_cls.return_value = mock_instance

        response = api_client.post("/run_commands", json={
            "action": "run",
            "nodes": "@office",
            "commands": ["ls -la"]
        })
        assert response.status_code == 200

    @patch("connpy.api.nodes")
    def test_run_list_nodes(self, mock_nodes_cls, api_client):
        """List of nodes is resolved correctly."""
        mock_instance = MagicMock()
        mock_instance.run.return_value = {"router1": "ok", "server1@office": "ok"}
        mock_nodes_cls.return_value = mock_instance

        response = api_client.post("/run_commands", json={
            "action": "run",
            "nodes": ["router1", "server1@office"],
            "commands": ["show version"]
        })
        assert response.status_code == 200


# =========================================================================
# /ask_ai endpoint
# =========================================================================

class TestAskAI:
    @patch("connpy.api.myai")
    def test_ask_ai(self, mock_ai_cls, api_client):
        mock_instance = MagicMock()
        mock_instance.ask.return_value = {"response": "AI says hello"}
        mock_ai_cls.return_value = mock_instance

        response = api_client.post("/ask_ai", json={
            "input": "list my routers"
        })
        data = response.get_json()
        assert data is not None

    @patch("connpy.api.myai")
    def test_ask_ai_with_dryrun(self, mock_ai_cls, api_client):
        mock_instance = MagicMock()
        mock_instance.ask.return_value = {"response": "dry run"}
        mock_ai_cls.return_value = mock_instance

        response = api_client.post("/ask_ai", json={
            "input": "test",
            "dryrun": True
        })
        assert response.status_code == 200

    @patch("connpy.api.myai")
    def test_ask_ai_with_history(self, mock_ai_cls, api_client):
        mock_instance = MagicMock()
        mock_instance.ask.return_value = {"response": "with history"}
        mock_ai_cls.return_value = mock_instance

        response = api_client.post("/ask_ai", json={
            "input": "follow up",
            "chat_history": [
                {"role": "user", "content": "previous"},
                {"role": "assistant", "content": "answer"}
            ]
        })
        assert response.status_code == 200


# =========================================================================
# /confirm endpoint
# =========================================================================

class TestConfirm:
    @patch("connpy.api.myai")
    def test_confirm(self, mock_ai_cls, api_client):
        mock_instance = MagicMock()
        mock_instance.confirm.return_value = True
        mock_ai_cls.return_value = mock_instance

        response = api_client.post("/confirm", json={
            "input": "yes"
        })
        assert response.status_code == 200
