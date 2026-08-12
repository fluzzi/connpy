import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from connpy.core import node
from connpy.cli.shell_handler import ShellHandler
from connpy.cli.validators import Validators

def test_local_protocol_get_cmd_and_connect():
    """Test protocol=local in node returns command string and spawns process."""
    n = node(unique="test_local", host="echo hello", protocol="local")
    assert n._get_cmd() == "echo hello"
    
    with patch("pexpect.spawn") as mock_spawn:
        mock_child = MagicMock()
        mock_child.child_fd = 10
        mock_spawn.return_value = mock_child
        with patch("pexpect.fdpexpect.fdspawn") as mock_fdspawn:
            res = n._connect()
            assert res is True
            mock_spawn.assert_called_once()

def test_shell_handler_dispatch(tmp_path):
    """Test ShellHandler builds transient node and calls interact."""
    app_mock = MagicMock()
    app_mock.config.config = {"shell": {"os": "ubuntu", "prompt": r"\$\s*$"}}
    
    handler = ShellHandler(app_mock)
    args = MagicMock()
    args.command_override = None
    args.capture_file = str(tmp_path / "session.log")
    args.debug = False
    
    with patch("connpy.cli.shell_handler.node") as mock_node_cls:
        mock_node = MagicMock()
        mock_node_cls.return_value = mock_node
        handler.dispatch(args)
        
        mock_node_cls.assert_called_once()
        kwargs = mock_node_cls.call_args.kwargs
        assert kwargs["protocol"] == "local"
        assert kwargs["unique"] == "local-shell"
        assert mock_node.interact.called

def test_validator_excludes_local_protocol():
    """Ensure protocol_validation does NOT accept 'local' for inventory forms."""
    validators = Validators(MagicMock())
    with pytest.raises(Exception):
        validators.protocol_validation({}, "local")

def test_is_child_connpy_active_non_local_protocol():
    """Ensure non-local protocols (e.g. ssh) never check for sub-child processes."""
    n = node(unique="ssh_node", host="10.0.0.1", protocol="ssh")
    with patch("os.tcgetpgrp") as mock_tcgetpgrp:
        assert n._is_child_connpy_active(10) is False
        mock_tcgetpgrp.assert_not_called()

def test_is_child_connpy_active_local_protocol():
    """Test detection of child connpy process when protocol=local."""
    n = node(unique="local_node", host="/bin/bash", protocol="local")
    
    # Case 1: child is connpy
    with patch("os.tcgetpgrp", return_value=1234), \
         patch("os.path.exists", return_value=True), \
         patch("builtins.open", MagicMock(return_value=MagicMock(__enter__=lambda s: MagicMock(read=lambda: b"python3\x00/usr/local/bin/connpy\x00connect\x00r1")))):
        assert n._is_child_connpy_active(10) is True

    # Case 2: child is regular bash
    with patch("os.tcgetpgrp", return_value=1234), \
         patch("os.path.exists", return_value=True), \
         patch("builtins.open", MagicMock(return_value=MagicMock(__enter__=lambda s: MagicMock(read=lambda: b"/bin/bash\x00")))):
        assert n._is_child_connpy_active(10) is False

    # Case 3: child is conn entry point (e.g. /home/fluzzi32/.local/bin/conn xr)
    with patch("os.tcgetpgrp", return_value=1234), \
         patch("os.path.exists", return_value=True), \
         patch("builtins.open", MagicMock(return_value=MagicMock(__enter__=lambda s: MagicMock(read=lambda: b"/usr/bin/python3\x00/home/fluzzi32/.local/bin/conn\x00xr")))):
        assert n._is_child_connpy_active(10) is True


