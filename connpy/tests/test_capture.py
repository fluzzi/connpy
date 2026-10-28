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

    def test_capture_module_lazy_loading_and_init_protocols(self, mock_connapp, RemoteCapture):
        from connpy.core_plugins.capture import Parser
        p = Parser()
        assert "SSH or Mosh" in p.parser.format_help()

        # Protocol ssh succeeds
        mock_connapp.services.nodes.list_nodes.return_value = ["ssh_node"]
        mock_connapp.services.nodes.get_node_details.return_value = {"host": "127.0.0.1", "protocol": "ssh"}
        ssh_node = MagicMock()
        ssh_node.protocol = "ssh"
        ssh_node.unique = "ssh_node"
        mock_connapp.node.return_value = ssh_node
        rc_ssh = RemoteCapture(mock_connapp, "ssh_node", "eth0")
        assert rc_ssh.node.protocol == "ssh"

        # Protocol mosh succeeds
        mock_connapp.services.nodes.list_nodes.return_value = ["mosh_node"]
        mock_connapp.services.nodes.get_node_details.return_value = {"host": "127.0.0.1", "protocol": "mosh"}
        mosh_node = MagicMock()
        mosh_node.protocol = "mosh"
        mosh_node.unique = "mosh_node"
        mock_connapp.node.return_value = mosh_node
        rc_mosh = RemoteCapture(mock_connapp, "mosh_node", "eth0")
        assert rc_mosh.node.protocol == "mosh"

        # Disallowed protocols exit with 2
        for proto in ["telnet", "docker", "ssm", "kubectl", "local"]:
            bad_node = MagicMock()
            bad_node.protocol = proto
            bad_node.unique = f"{proto}_node"
            mock_connapp.node.return_value = bad_node
            mock_connapp.services.nodes.list_nodes.return_value = [f"{proto}_node"]
            mock_connapp.services.nodes.get_node_details.return_value = {"host": "127.0.0.1", "protocol": proto}
            with patch("connpy.printer.error") as mock_err:
                with pytest.raises(SystemExit) as exc:
                    RemoteCapture(mock_connapp, f"{proto}_node", "eth0")
                assert exc.value.code == 2
                assert mock_err.called

    def test_run_mosh_node_prepends_tunnel_into_copied_tags_forces_ssh_and_restores_state(self, mock_connapp, RemoteCapture):
        orig_tags = {"mosh_ssh_options": "-i /tmp/key -4"}
        orig_options = ""
        node_mock = MagicMock()
        node_mock.protocol = "mosh"
        node_mock.unique = "mosh_box"
        node_mock.host = "10.0.0.1"
        node_mock.user = "admin"
        node_mock.port = "22"
        node_mock.tags = orig_tags
        node_mock.options = orig_options

        connect_tags_observed = []
        connect_call_args = []

        def fake_connect(*args, **kwargs):
            connect_tags_observed.append(dict(node_mock.tags))
            connect_call_args.append((args, kwargs))
            return True

        node_mock._connect.side_effect = fake_connect
        node_mock.child = MagicMock()

        mock_connapp.services.nodes.list_nodes.return_value = ["mosh_box"]
        mock_connapp.services.nodes.get_node_details.return_value = {"host": "10.0.0.1", "protocol": "mosh"}
        mock_connapp.node.return_value = node_mock

        rc = RemoteCapture(mock_connapp, "mosh_box", "eth0")
        with patch.object(rc, "_find_free_port", return_value=25555), \
             patch.object(rc, "_detect_sudo_requirement", return_value=False), \
             patch.object(rc, "_start_local_listener"), \
             patch.object(rc, "_sendline_until_connected", return_value=True), \
             patch("time.sleep"):
            def stop_loop(*_a, **_kw):
                rc.listener_active = False
                rc.listener_conn = None
            rc._start_local_listener.side_effect = stop_loop
            rc.run()

        # Verify force_ssh and allow_reverse_tunnel_port
        assert len(connect_call_args) == 1
        _, kwargs = connect_call_args[0]
        assert kwargs.get("force_ssh") is True
        assert kwargs.get("allow_reverse_tunnel_port") == 25555

        # Verify mosh_ssh_options prepended tunnel options into copied tags
        assert len(connect_tags_observed) == 1
        assert connect_tags_observed[0]["mosh_ssh_options"] == "-o ExitOnForwardFailure=yes -R 25555:localhost:25555 -i /tmp/key -4"

        # Verify exact identity restoration (is)
        assert node_mock.tags is orig_tags
        assert node_mock.options is orig_options

        # Case: tags is non-dict (e.g. empty string)
        node_mock.tags = ""
        node_mock.options = orig_options
        with patch.object(rc, "_find_free_port", return_value=25555), \
             patch.object(rc, "_detect_sudo_requirement", return_value=False), \
             patch.object(rc, "_start_local_listener"), \
             patch.object(rc, "_sendline_until_connected", return_value=True), \
             patch("time.sleep"):
            rc._start_local_listener.side_effect = stop_loop
            rc.run()
        assert node_mock.tags == ""
        assert node_mock.options is orig_options

        # State restoration when _find_free_port fails
        node_mock.tags = orig_tags
        node_mock.options = orig_options
        with patch.object(rc, "_find_free_port", side_effect=RuntimeError("port err")):
            with pytest.raises(RuntimeError):
                rc.run()
        assert node_mock.tags is orig_tags
        assert node_mock.options is orig_options

        # State restoration when _connect() fails (connection error)
        node_mock.tags = orig_tags
        node_mock.options = orig_options
        node_mock._connect.side_effect = None
        node_mock._connect.return_value = "Connection refused"
        with patch.object(rc, "_find_free_port", return_value=25555), \
             patch("connpy.printer.error") as mock_err:
            with pytest.raises(SystemExit) as exc:
                rc.run()
            assert exc.value.code == 1
            mock_err.assert_called_once()
            assert "Could not connect" in mock_err.call_args[0][0]
        assert node_mock.tags is orig_tags
        assert node_mock.options is orig_options

        # State restoration on KeyboardInterrupt
        node_mock.tags = orig_tags
        node_mock.options = orig_options
        node_mock._connect.return_value = True
        rc.listener_conn = None
        with patch.object(rc, "_find_free_port", return_value=25555), \
             patch.object(rc, "_detect_sudo_requirement", return_value=False), \
             patch.object(rc, "_start_local_listener") as mock_listener, \
             patch.object(rc, "_sendline_until_connected", return_value=True), \
             patch("time.sleep", side_effect=KeyboardInterrupt):
            def kb_listener(*_a, **_kw):
                rc.listener_active = False
                rc.listener_conn = None
            mock_listener.side_effect = kb_listener
            rc.run()
        assert node_mock.tags is orig_tags
        assert node_mock.options is orig_options

        # State restoration on SystemExit
        node_mock.tags = orig_tags
        node_mock.options = orig_options
        rc.listener_conn = None
        with patch.object(rc, "_find_free_port", return_value=25555), \
             patch.object(rc, "_detect_sudo_requirement", side_effect=SystemExit(9)):
            with pytest.raises(SystemExit) as exc:
                rc.run()
            assert exc.value.code == 9
        assert node_mock.tags is orig_tags
        assert node_mock.options is orig_options

        # Invalid Mosh SSH configuration causes sys.exit(2) and single printer.error
        bad_tags = {"mosh_ssh_options": "-o ProxyCommand=id"}
        node_mock.tags = bad_tags
        node_mock.options = orig_options
        with patch.object(rc, "_find_free_port", return_value=25555), \
             patch("connpy.printer.error") as mock_err:
            with pytest.raises(SystemExit) as exc:
                rc.run()
            assert exc.value.code == 2
            mock_err.assert_called_once()
            assert "Invalid Mosh SSH configuration" in mock_err.call_args[0][0]
        assert node_mock.tags is bad_tags
        assert node_mock.options is orig_options

    def test_help_text_and_docstring_mention_mosh_jumphost_and_capture(self):
        import connpy
        from connpy.cli.help_text import get_instructions

        help_add = get_instructions("add").lower()
        assert "mosh" in help_add
        assert "jumphost" in help_add
        assert "capture" in help_add

        pkg_doc = (connpy.__doc__ or "").lower()
        assert "mosh" in pkg_doc
        assert "jumphost" in pkg_doc
        assert "capture" in pkg_doc
