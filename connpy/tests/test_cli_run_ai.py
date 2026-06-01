import pytest
from unittest.mock import patch, MagicMock, ANY
from connpy.connapp import connapp
import os

@pytest.fixture
def app(populated_config):
    """Returns an instance of connapp initialized with mock config."""
    return connapp(populated_config)

def test_run_generate_ai_dispatch(app):
    """Test that connpy run --generate-ai parses and calls ai_generate."""
    with patch("connpy.cli.run_handler.RunHandler.ai_generate") as mock_ai_gen:
        app.start(["run", "--generate-ai", "new_playbook.yaml"])
        mock_ai_gen.assert_called_once()
        args = mock_ai_gen.call_args[0][0]
        assert args.data == ["new_playbook.yaml"]
        assert args.action == "generate_ai"

def test_run_preflight_ai_node(app):
    """Test that connpy run --preflight-ai calls predict_execution_results and exits."""
    with patch("connpy.services.node_service.NodeService.list_nodes", return_value=["router1"]):
        with patch("connpy.services.ai_service.AIService.predict_execution_results") as mock_predict:
            with pytest.raises(SystemExit) as exc:
                app.start(["run", "router1", "show version", "--preflight-ai"])
            
            assert exc.value.code == 0
            mock_predict.assert_called_once_with(["router1"], ["show version"], chunk_callback=ANY)

def test_run_analyze_node(app):
    """Test that connpy run --analyze calls analyze_execution_results after execution."""
    mock_run = MagicMock(return_value={"router1": {"status": 0, "output": "success"}})
    
    with patch("connpy.services.node_service.NodeService.list_nodes", return_value=["router1"]):
        with patch("connpy.services.execution_service.ExecutionService.run_commands", mock_run):
            with patch("connpy.services.ai_service.AIService.analyze_execution_results") as mock_analyze:
                app.start(["run", "router1", "show version", "--analyze"])
                mock_run.assert_called_once()
                mock_analyze.assert_called_once_with(
                    {"router1": {"status": 0, "output": "success"}},
                    query="show version",
                    chunk_callback=ANY
                )

def test_run_preflight_ai_playbook(app, tmp_path):
    """Test that running a playbook with --preflight-ai predicts results per task."""
    playbook_path = tmp_path / "test_playbook.yaml"
    playbook_content = """
tasks:
  - name: test-task
    action: run
    nodes: "router1"
    commands: ["show ip interface brief"]
    output: stdout
"""
    playbook_path.write_text(playbook_content)
    
    with patch("connpy.services.node_service.NodeService.list_nodes", return_value=["router1"]):
        with patch("connpy.services.ai_service.AIService.predict_execution_results") as mock_predict:
            with pytest.raises(SystemExit) as exc:
                app.start(["run", str(playbook_path), "--preflight-ai"])
            
            assert exc.value.code == 0
            mock_predict.assert_called_once_with(["router1"], ["show ip interface brief"], chunk_callback=ANY)

def test_run_analyze_playbook(app, tmp_path):
    """Test that running a playbook with --analyze triggers strategic analysis on all task outcomes."""
    playbook_path = tmp_path / "test_playbook.yaml"
    playbook_content = """
tasks:
  - name: test-task
    action: run
    nodes: "router1"
    commands: ["show ip interface brief"]
    output: stdout
"""
    playbook_path.write_text(playbook_content)
    
    mock_run = MagicMock(return_value={"router1": {"status": 0, "output": "ok"}})
    
    with patch("connpy.services.node_service.NodeService.list_nodes", return_value=["router1"]):
        with patch("connpy.services.execution_service.ExecutionService.run_commands", mock_run):
            with patch("connpy.services.ai_service.AIService.analyze_execution_results") as mock_analyze:
                app.start(["run", str(playbook_path), "--analyze"])
                mock_run.assert_called_once()
                mock_analyze.assert_called_once_with(
                    {"router1": {"status": 0, "output": "ok"}},
                    query=f"Playbook: {str(playbook_path)}",
                    chunk_callback=ANY
                )

def test_ai_generate_wizard_save(app, tmp_path):
    """Test that ai_generate wizard runs interactive chat loop, asks for validation and saves YAML."""
    dest_yaml = tmp_path / "playbook.yaml"
    
    mock_chat = MagicMock(return_value={
        "response": "Here is your playbook.",
        "chat_history": [],
        "playbook_yaml": "tasks:\n  - name: mytask"
    })
    app.services.ai.build_playbook_chat = mock_chat
    
    # Mock rich.prompt.Prompt.ask to simulate User inputting prompt and then 'y' to save
    with patch("rich.prompt.Prompt.ask", side_effect=["create a basic task", "y"]):
        app.start(["run", "--generate-ai", str(dest_yaml)])
        
        mock_chat.assert_called_once_with("create a basic task", chat_history=[], chunk_callback=ANY)
        assert os.path.exists(dest_yaml)
        with open(dest_yaml) as f:
            content = f.read()
            assert "tasks:" in content

def test_ai_generate_wizard_run(app, tmp_path):
    """Test that ai_generate wizard runs, saves the playbook and executes it when choosing 'run'."""
    dest_yaml = tmp_path / "playbook_run.yaml"
    
    mock_chat = MagicMock(return_value={
        "response": "Here is your playbook.",
        "chat_history": [],
        "playbook_yaml": "tasks:\n  - name: mytask\n    action: run\n    nodes: '*'\n    commands: ['show version']\n    output: stdout"
    })
    app.services.ai.build_playbook_chat = mock_chat
    
    with patch("rich.prompt.Prompt.ask", side_effect=["create task", "run"]):
        with patch("connpy.cli.run_handler.RunHandler.yaml_run") as mock_yaml_run:
            app.start(["run", "--generate-ai", str(dest_yaml)])
            
            mock_chat.assert_called_once_with("create task", chat_history=[], chunk_callback=ANY)
            assert os.path.exists(dest_yaml)
            with open(dest_yaml) as f:
                content = f.read()
                assert "tasks:" in content
            
            mock_yaml_run.assert_called_once()
            args = mock_yaml_run.call_args[0][0]
            assert args.data == [str(dest_yaml)]
