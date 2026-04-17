"""Tests for connpy.core_plugins.capture"""
import pytest
from unittest.mock import MagicMock, patch
from connpy.core_plugins.capture import Entrypoint

@pytest.fixture
def RemoteCapture():
    return Entrypoint.get_remote_capture_class()

@pytest.fixture
def mock_connapp():
    app = MagicMock()
    app.services.nodes.list_nodes.return_value = ["test_node"]
    app.services.nodes.get_node_details.return_value = {"host": "127.0.0.1", "protocol": "ssh"}
    app.services.config_svc.get_settings().get.return_value = "/fake/ws"
    
    mock_node = MagicMock()
    mock_node.protocol = "ssh"
    mock_node.unique = "test_node"
    app.node.return_value = mock_node
    return app

class TestRemoteCapture:
    def test_init_node_not_found(self, mock_connapp, RemoteCapture):
        # Attempt to capture a node not in inventory
        mock_connapp.services.nodes.list_nodes.return_value = []
        with pytest.raises(SystemExit) as exc:
            RemoteCapture(mock_connapp, "test_node", "eth0")
        assert exc.value.code == 2

    def test_init_success(self, mock_connapp, RemoteCapture):
        rc = RemoteCapture(mock_connapp, "test_node", "eth0")
        assert rc.node_name == "test_node"
        assert rc.interface == "eth0"
        assert rc.wireshark_path == "/fake/ws"

    def test_is_port_in_use(self, mock_connapp, RemoteCapture):
        rc = RemoteCapture(mock_connapp, "test_node", "eth0")
        with patch("socket.socket") as mock_socket:
            mock_sock_instance = MagicMock()
            mock_socket.return_value.__enter__.return_value = mock_sock_instance
            
            mock_sock_instance.connect_ex.return_value = 0
            assert rc._is_port_in_use(8080) is True
            
            mock_sock_instance.connect_ex.return_value = 1
            assert rc._is_port_in_use(8080) is False

    def test_find_free_port(self, mock_connapp, RemoteCapture):
        rc = RemoteCapture(mock_connapp, "test_node", "eth0")
        with patch.object(RemoteCapture, "_is_port_in_use") as mock_is_in_use:
            # First 2 ports in use, 3rd is free
            mock_is_in_use.side_effect = [True, True, False]
            port = rc._find_free_port(20000, 30000)
            assert 20000 <= port <= 30000
            assert mock_is_in_use.call_count == 3
