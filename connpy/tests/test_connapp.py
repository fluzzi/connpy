import pytest
from unittest.mock import patch, MagicMock
from connpy.connapp import connapp
import sys
import yaml
import os

@pytest.fixture
def app(populated_config):
    """Returns an instance of connapp initialized with the mock config."""
    return connapp(populated_config)

def test_connapp_init(app, populated_config):
    """Test that connapp initializes correctly with config."""
    assert app.config == populated_config
    assert app.case == populated_config.config.get("case", False)

@patch("connpy.cli.node_handler.NodeHandler.dispatch")
def test_node_default(mock_func_node, app):
    """Test that default 'node' command correctly parses and calls _func_node."""
    app.start(["node", "router1"])
    mock_func_node.assert_called_once()
    args = mock_func_node.call_args[0][0]
    assert args.data == "router1"
    assert args.action == "connect"

@patch("connpy.cli.node_handler.NodeHandler.dispatch")
def test_node_add(mock_func_node, app):
    """Test that 'node -a' command correctly parses."""
    app.start(["node", "-a", "new_router"])
    mock_func_node.assert_called_once()
    args = mock_func_node.call_args[0][0]
    assert args.data == "new_router"
    assert args.action == "add"

@patch("connpy.services.node_service.NodeService.list_nodes")
@patch("connpy.services.node_service.NodeService.delete_node")
@patch("inquirer.prompt")
def test_node_del(mock_prompt, mock_delete_node, mock_list_nodes, app):
    mock_list_nodes.return_value = ["router1"]
    mock_prompt.return_value = {"delete": True}
    app.start(["node", "-r", "router1"])
    mock_delete_node.assert_called_once_with("router1", is_folder=False)

@patch("connpy.services.node_service.NodeService.list_nodes")
@patch("connpy.services.node_service.NodeService.get_node_details")
@patch("connpy.services.node_service.NodeService.update_node")
@patch("connpy.cli.forms.Forms.questions_edit")
@patch("connpy.cli.forms.Forms.questions_nodes")
def test_node_mod(mock_q_nodes, mock_q_edit, mock_update_node, mock_get_details, mock_list_nodes, app):
    mock_list_nodes.return_value = ["router1"]
    mock_get_details.return_value = {"host": "1.1.1.1", "port": 22}
    mock_q_edit.return_value = {"host": True}
    mock_q_nodes.return_value = {"host": "2.2.2.2", "port": 22}

    app.start(["node", "-e", "router1"])
    mock_update_node.assert_called_once()

@patch("connpy.printer.data")
def test_node_show(mock_data, app):
    app.nodes_list = ["router1"]
    app.config.getitem = MagicMock(return_value={"host": "1.1.1.1"})
    app.start(["node", "-s", "router1"])
    mock_data.assert_called()

@patch("connpy.services.profile_service.ProfileService.list_profiles")
@patch("connpy.connapp.printer.console.print")
def test_profile_list(mock_print, mock_list_profiles, app):
    """Test 'profile list' invokes profile service correctly."""
    mock_list_profiles.return_value = ["default", "office-user"]
    app.start(["list", "profiles"])
    assert mock_list_profiles.call_count >= 2

@patch("connpy.services.node_service.NodeService.list_nodes")
def test_node_list(mock_list_nodes, app):
    """Test 'list nodes' invokes node service."""
    mock_list_nodes.return_value = ["router1", "server1"]
    app.start(["list", "nodes"])
    # Should be called during init and during the list command
    assert mock_list_nodes.call_count >= 2

@patch("connpy.services.system_service.SystemService.get_api_status")
def test_api_stop(mock_status, app):
    mock_status.return_value = {"running": True, "pid": "1234"}
    app.services.system.stop_api = MagicMock(return_value=True)
    app.start(["api", "-x"])
    app.services.system.stop_api.assert_called_once()

@patch("connpy.services.profile_service.ProfileService.list_profiles")
@patch("connpy.services.profile_service.ProfileService.add_profile")
@patch("connpy.cli.forms.Forms.questions_profiles")
def test_profile_add(mock_q_profiles, mock_add_profile, mock_list_profiles, app):
    mock_list_profiles.return_value = ["default"]
    mock_q_profiles.return_value = {"host": "test"}
    app.start(["profile", "-a", "new_profile"])
    mock_add_profile.assert_called_once_with("new_profile", {"host": "test"})

@patch("connpy.services.profile_service.ProfileService.get_profile")
@patch("connpy.services.profile_service.ProfileService.delete_profile")
@patch("inquirer.prompt")
def test_profile_del(mock_prompt, mock_delete_profile, mock_get_profile, app):
    mock_get_profile.return_value = {"host": "test"}
    mock_prompt.return_value = {"delete": True}
    app.start(["profile", "-r", "test_profile"])
    mock_delete_profile.assert_called_once_with("test_profile")

@patch("connpy.services.profile_service.ProfileService.get_profile")
@patch("connpy.services.profile_service.ProfileService.update_profile")
@patch("connpy.cli.forms.Forms.questions_edit")
@patch("connpy.cli.forms.Forms.questions_profiles")
def test_profile_mod(mock_q_profiles, mock_q_edit, mock_update_profile, mock_get_profile, app):
    mock_get_profile.return_value = {"host": "test", "port": 22}
    mock_q_edit.return_value = {"host": True}
    mock_q_profiles.return_value = {"id": "test_profile", "host": "new_host", "port": 22}
    app.start(["profile", "-e", "test_profile"])
    mock_update_profile.assert_called_once_with("test_profile", {"id": "test_profile", "host": "new_host", "port": 22})

@patch("connpy.services.profile_service.ProfileService.get_profile")
@patch("connpy.printer.data")
def test_profile_show(mock_data, mock_get_profile, app):
    mock_get_profile.return_value = {"host": "test"}
    app.start(["profile", "-s", "test_profile"])
    mock_data.assert_called()

@patch("connpy.services.node_service.NodeService.move_node")
def test_move(mock_move_node, app):
    app.start(["move", "src_node", "dst_node"])
    mock_move_node.assert_called_once_with("src_node", "dst_node", copy=False)

@patch("connpy.services.node_service.NodeService.move_node")
def test_copy(mock_move_node, app):
    app.start(["copy", "src_node", "dst_node"])
    mock_move_node.assert_called_once_with("src_node", "dst_node", copy=True)

@patch("connpy.cli.forms.Forms.questions_bulk")
@patch("connpy.services.node_service.NodeService.bulk_add")
def test_bulk(mock_bulk_add, mock_q_bulk, app):
    mock_q_bulk.return_value = {"ids": "node1", "host": "host1", "location": ""}
    mock_bulk_add.return_value = 1
    app.start(["bulk"])
    mock_bulk_add.assert_called_once()

@patch("connpy.services.import_export_service.ImportExportService.export_to_file")
def test_export(mock_export, app):
    with pytest.raises(SystemExit):
        app.start(["export", "file.yml", "@folder1"])
    mock_export.assert_called_once_with("file.yml", folders=["@folder1"])

@patch("os.path.exists")
@patch("inquirer.prompt")
@patch("connpy.services.import_export_service.ImportExportService.import_from_file")
def test_import(mock_import, mock_prompt, mock_exists, app):
    mock_exists.return_value = True
    mock_prompt.return_value = {"import": True}
    app.start(["import", "file.yml"])
    mock_import.assert_called_once_with("file.yml")

@patch("connpy.services.ai_service.AIService.ask")
@patch("connpy.connapp.console.status")
def test_ai(mock_status, mock_ask, app):
    mock_ask.return_value = {"response": "AI output", "usage": {"total": 10, "input": 5, "output": 5}}
    
    app.start(["ai", "--engineer-api-key", "testkey", "how are you"])
    mock_ask.assert_called_once()

@patch("connpy.services.execution_service.ExecutionService.run_commands")
def test_run(mock_run_commands, app):
    app.start(["run", "node1", "command1", "command2"])
    mock_run_commands.assert_called_once()
    assert mock_run_commands.call_args[1]["nodes_filter"] == "node1"
    assert mock_run_commands.call_args[1]["commands"] == ["command1 command2"]

@patch("os.path.exists")
@patch("shutil.copy2")
@patch("connpy.plugins.Plugins.verify_script")
def test_plugin_add(mock_verify, mock_copy, mock_exists, app):
    def mock_exists_side_effect(path):
        if "testplug.py" in path: return False
        if "testplug.py.bkp" in path: return False
        if "file.py" in path: return True
        return True
    mock_exists.side_effect = mock_exists_side_effect
    mock_verify.return_value = None
    app.commands = []
    app.start(["plugin", "--add", "testplug", "file.py"])
    mock_copy.assert_called()

@patch("connpy.services.config_service.ConfigService.update_setting")
def test_config(mock_update_setting, app):
    app.start(["config", "--allow-uppercase", "true"])
    mock_update_setting.assert_called_with("case", True)

@patch("connpy.services.system_service.SystemService.get_api_status")
def test_api_start(mock_status, app):
    mock_status.return_value = {"running": False}
    app.services.system.start_api = MagicMock()
    app.start(["api", "-s", "8080"])
    app.services.system.start_api.assert_called_once_with(port=8080)

@patch("connpy.services.system_service.SystemService.get_api_status")
def test_api_debug(mock_status, app):
    mock_status.return_value = {"running": False}
    app.services.system.debug_api = MagicMock()
    app.start(["api", "-d", "8080"])
    app.services.system.debug_api.assert_called_once_with(port=8080)

@patch("connpy.services.node_service.NodeService.list_folders")
def test_list_folders(mock_list_folders, app):
    mock_list_folders.return_value = ["folder1"]
    app.start(["list", "folders"])
    # Called during init and during the list command
    assert mock_list_folders.call_count >= 2

@patch("connpy.services.config_service.ConfigService.update_setting")
def test_config_various(mock_update_setting, app):
    app.start(["config", "--fzf", "true"])
    mock_update_setting.assert_called_with("fzf", True)
    app.start(["config", "--keepalive", "60"])
    mock_update_setting.assert_called_with("idletime", 60)

@patch("connpy.services.config_service.ConfigService.set_config_folder")
def test_config_folder(mock_set_config_folder, app):
    app.start(["config", "--configfolder", "/new/path"])
    mock_set_config_folder.assert_called_once_with("/new/path")

@patch("connpy.services.plugin_service.PluginService.list_plugins")
def test_plugin_list(mock_list_plugins, app):
    mock_list_plugins.return_value = {"testplug": {"enabled": True}}
    app.start(["plugin", "--list"])
    mock_list_plugins.assert_called_once()

@patch("connpy.services.plugin_service.PluginService.delete_plugin")
def test_plugin_delete(mock_delete, app):
    app.start(["plugin", "--del", "testplug"])
    mock_delete.assert_called_once_with("testplug")

@patch("connpy.services.plugin_service.PluginService.enable_plugin")
def test_plugin_enable(mock_enable, app):
    app.start(["plugin", "--enable", "testplug"])
    mock_enable.assert_called_once_with("testplug")

@patch("connpy.services.plugin_service.PluginService.disable_plugin")
def test_plugin_disable(mock_disable, app):
    app.start(["plugin", "--disable", "testplug"])
    mock_disable.assert_called_once_with("testplug")

@patch("connpy.services.ai_service.AIService.list_sessions")
def test_ai_list(mock_list_sessions, app):
    mock_list_sessions.return_value = [{"id": "1", "title": "t", "created_at": "now", "model": "m"}]
    app.start(["ai", "--list"])
    mock_list_sessions.assert_called_once()

def test_type_node_reserved_word(app):
    app.commands = ["bulk", "ai", "run"]
    with patch("sys.argv", ["connpy", "node", "-a", "bulk"]):
        with pytest.raises(SystemExit) as exc:
            app._type_node("bulk")
        assert exc.value.code == 2
    
    # In move/copy it also raises because destination cannot be reserved
    with patch("sys.argv", ["connpy", "mv", "test1", "bulk"]):
        with pytest.raises(SystemExit) as exc:
            app._type_node("bulk")
        assert exc.value.code == 2
