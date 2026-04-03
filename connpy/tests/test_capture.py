"""Tests for connpy.core_plugins.capture"""
import pytest
from unittest.mock import MagicMock, patch
from connpy.core_plugins.capture import RemoteCapture

@pytest.fixture
def mock_connapp():
    app = MagicMock()
    app.nodes_list = ["test_node"]
    app.config.getitem.return_value = {"host": "127.0.0.1", "protocol": "ssh"}
    mock_node = MagicMock()
    mock_node.protocol = "ssh"
    mock_node.unique = "test_node"
    app.node.return_value = mock_node
    app.config.config = {"wireshark_path": "/fake/ws"}
    return app

class TestRemoteCapture:
    def test_init_node_not_found(self, mock_connapp):
        # Attempt to capture a node not in nodes_list
        mock_connapp.nodes_list = ["other_node"]
        with pytest.raises(SystemExit) as exc:
            RemoteCapture(mock_connapp, "test_node", "eth0")
        assert exc.value.code == 2

    def test_init_success(self, mock_connapp):
        rc = RemoteCapture(mock_connapp, "test_node", "eth0")
        assert rc.node_name == "test_node"
        assert rc.interface == "eth0"
        assert rc.wireshark_path == "/fake/ws"

    @patch("connpy.core_plugins.capture.socket")
    def test_is_port_in_use(self, mock_socket, mock_connapp):
        rc = RemoteCapture(mock_connapp, "test_node", "eth0")
        mock_sock_instance = MagicMock()
        mock_socket.socket.return_value.__enter__.return_value = mock_sock_instance
        
        mock_sock_instance.connect_ex.return_value = 0
        assert rc._is_port_in_use(8080) is True
        
        mock_sock_instance.connect_ex.return_value = 1
        assert rc._is_port_in_use(8080) is False

    @patch.object(RemoteCapture, "_is_port_in_use")
    def test_find_free_port(self, mock_is_in_use, mock_connapp):
        rc = RemoteCapture(mock_connapp, "test_node", "eth0")
        # First 2 ports in use, 3rd is free
        mock_is_in_use.side_effect = [True, True, False]
        port = rc._find_free_port(20000, 30000)
        assert 20000 <= port <= 30000
        assert mock_is_in_use.call_count == 3
