"""Tests for connpy.core module — node and nodes classes."""
import json
import os
import io
import re
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from copy import deepcopy


# =========================================================================
# node.__init__ tests
# =========================================================================

class TestNodeInit:
    def test_basic_init(self):
        """Creates node with basic attributes."""
        from connpy.core import node
        n = node("router1", "10.0.0.1", user="admin", password="pass1", protocol="ssh")
        assert n.unique == "router1"
        assert n.host == "10.0.0.1"
        assert n.user == "admin"
        assert n.protocol == "ssh"
        assert n.password == ["pass1"]

    def test_default_protocol(self):
        """Default protocol is ssh."""
        from connpy.core import node
        n = node("router1", "10.0.0.1")
        assert n.protocol == "ssh"

    def test_password_as_list_of_profiles(self, populated_config):
        """Password list with @profile references resolves correctly."""
        from connpy.core import node
        n = node("router1", "10.0.0.1", password=["@office-user"],
                 config=populated_config)
        assert n.password == ["officepass"]

    def test_password_plain_string(self):
        """Plain string password is wrapped in a list."""
        from connpy.core import node
        n = node("router1", "10.0.0.1", password="mypass")
        assert n.password == ["mypass"]

    def test_node_with_profile(self, populated_config):
        """Resolves @profile references for user."""
        from connpy.core import node
        n = node("test1", "10.0.0.1", user="@office-user", password="plain",
                 config=populated_config)
        assert n.user == "officeadmin"

    def test_node_tags(self):
        """Tags are stored correctly."""
        from connpy.core import node
        tags = {"os": "cisco_ios", "prompt": r"Router#"}
        n = node("router1", "10.0.0.1", tags=tags)
        assert n.tags["os"] == "cisco_ios"


# =========================================================================
# Command generation tests
# =========================================================================

class TestCommandGeneration:
    def _make_node(self, **kwargs):
        from connpy.core import node
        defaults = {
            "unique": "test", "host": "10.0.0.1", "protocol": "ssh",
            "user": "admin", "password": "", "port": "", "options": "",
            "jumphost": "", "tags": "", "logs": ""
        }
        defaults.update(kwargs)
        return node(defaults.pop("unique"), defaults.pop("host"), **defaults)

    def test_ssh_cmd_basic(self):
        n = self._make_node()
        cmd = n._get_cmd()
        assert "ssh" in cmd
        assert "admin@10.0.0.1" in cmd

    def test_ssh_cmd_port(self):
        n = self._make_node(port="2222")
        cmd = n._get_cmd()
        assert "-p 2222" in cmd

    def test_ssh_cmd_options(self):
        n = self._make_node(options="-o StrictHostKeyChecking=no")
        cmd = n._get_cmd()
        assert "-o StrictHostKeyChecking=no" in cmd

    def test_sftp_cmd_port(self):
        n = self._make_node(protocol="sftp", port="2222")
        cmd = n._get_cmd()
        assert "-P 2222" in cmd  # SFTP uses uppercase P

    def test_telnet_cmd(self):
        n = self._make_node(protocol="telnet", port="23")
        cmd = n._get_cmd()
        assert "telnet 10.0.0.1" in cmd
        assert "23" in cmd

    def test_ssm_cmd_basic(self):
        n = self._make_node(protocol="ssm", host="i-12345")
        cmd = n._get_cmd()
        assert "aws ssm start-session" in cmd
        assert "--target i-12345" in cmd

    def test_ssm_cmd_tags(self):
        n = self._make_node(protocol="ssm", host="i-12345", tags={"region": "us-west-2", "profile": "prod"})
        cmd = n._get_cmd()
        assert "--region us-west-2" in cmd
        assert "--profile prod" in cmd

    def test_ssm_cmd_options(self):
        n = self._make_node(protocol="ssm", host="i-12345", options="--document-name AWS-StartInteractiveCommand")
        cmd = n._get_cmd()
        assert "--document-name AWS-StartInteractiveCommand" in cmd

    def test_kubectl_cmd(self):
        n = self._make_node(protocol="kubectl", host="my-pod", tags={"kube_command": "/bin/sh"})
        cmd = n._get_cmd()
        assert "kubectl exec" in cmd
        assert "my-pod" in cmd
        assert "/bin/sh" in cmd

    def test_kubectl_cmd_default_command(self):
        n = self._make_node(protocol="kubectl", host="my-pod")
        cmd = n._get_cmd()
        assert "/bin/bash" in cmd

    def test_docker_cmd(self):
        n = self._make_node(protocol="docker", host="my-container",
                           tags={"docker_command": "/bin/sh"})
        cmd = n._get_cmd()
        assert "docker" in cmd
        assert "my-container" in cmd
        assert "/bin/sh" in cmd

    def test_invalid_protocol_raises(self):
        n = self._make_node(protocol="invalid_proto")
        with pytest.raises(SystemExit) as exc:
            n._get_cmd()
        assert exc.value.code == 1

    def test_ssh_cmd_no_user(self):
        n = self._make_node(user="")
        cmd = n._get_cmd()
        assert "10.0.0.1" in cmd
        assert "@" not in cmd  # No user@ prefix


# =========================================================================
# Password decryption tests
# =========================================================================

class TestPasswordDecryption:
    def test_passtx_plaintext(self, config):
        """Plaintext passwords pass through unchanged."""
        from connpy.core import node
        n = node("test", "10.0.0.1", password="plainpass", config=config)
        result = n._passtx(["plainpass"])
        assert result == ["plainpass"]

    def test_passtx_encrypted(self, config):
        """Encrypted passwords get decrypted."""
        from connpy.core import node
        encrypted = config.encrypt("mysecret")
        n = node("test", "10.0.0.1", password=encrypted, config=config)
        result = n._passtx([encrypted])
        assert result == ["mysecret"]

    def test_passtx_missing_key_raises(self):
        """Missing key file raises ValueError."""
        from connpy.core import node
        n = node("test", "10.0.0.1", password="pass")
        # A password formatted as encrypted but no valid key
        with pytest.raises((ValueError, Exception)):
            n._passtx(["""b'corrupted_encrypted_data'"""], keyfile="/nonexistent")


# =========================================================================
# Log handling tests
# =========================================================================

class TestLogHandling:
    def test_logfile_variable_substitution(self):
        from connpy.core import node
        n = node("router1", "10.0.0.1", user="admin", protocol="ssh", port="22",
                 logs="/logs/${unique}_${host}_${user}")
        result = n._logfile()
        assert result == "/logs/router1_10.0.0.1_admin"

    def test_logfile_date_substitution(self):
        from connpy.core import node
        import datetime
        n = node("router1", "10.0.0.1", logs="/logs/${date '%Y'}")
        result = n._logfile()
        assert datetime.datetime.now().strftime("%Y") in result

    def test_logclean_removes_ansi(self):
        from connpy.core import node
        n = node("test", "10.0.0.1")
        dirty = "\x1B[32mgreen text\x1B[0m"
        clean = n._logclean(dirty, var=True)
        assert "\x1B" not in clean
        assert "green text" in clean

    def test_logclean_removes_backspaces(self):
        from connpy.core import node
        n = node("test", "10.0.0.1")
        dirty = "type\bo"
        clean = n._logclean(dirty, var=True)
        assert "\b" not in clean


# =========================================================================
# run() and test() with mock pexpect
# =========================================================================

class TestNodeRun:
    def _make_connected_node(self, mock_pexpect_obj, **kwargs):
        """Create a node and mock its _connect to succeed."""
        from connpy.core import node
        defaults = {
            "unique": "router1", "host": "10.0.0.1",
            "protocol": "ssh", "user": "admin", "password": ""
        }
        defaults.update(kwargs)
        n = node(defaults.pop("unique"), defaults.pop("host"), **defaults)
        return n

    def test_run_returns_output(self, mock_pexpect):
        """run() returns string output."""
        child = mock_pexpect["child"]
        pexp = mock_pexpect["pexpect"]

        # Simulate: connect succeeds, command runs, prompt found
        child.expect.return_value = 9  # prompt index for ssh
        child.logfile_read = None

        from connpy.core import node
        n = node("router1", "10.0.0.1", user="admin", password="")

        # Mock _connect to return True and set up child
        with patch.object(n, '_connect', return_value=True):
            n.child = child
            log_buffer = io.BytesIO(b"show version\nRouter v1.0\nrouter#")
            n.mylog = log_buffer
            child.logfile_read = log_buffer

            with patch.object(n, '_logclean', return_value="Router v1.0"):
                output = n.run(["show version"])

        assert n.status == 0
        assert output == "Router v1.0"

    def test_run_status_1_on_failure(self, mock_pexpect):
        """Status 1 when connection fails."""
        from connpy.core import node
        n = node("router1", "10.0.0.1", user="admin", password="")

        with patch.object(n, '_connect', return_value="Connection failed code: 1\nrefused"):
            output = n.run(["show version"])

        assert n.status == 1
        assert "refused" in output

    def test_run_with_variables(self, mock_pexpect):
        """Variables get substituted in commands."""
        child = mock_pexpect["child"]
        child.expect.return_value = 9

        from connpy.core import node
        n = node("router1", "10.0.0.1", user="admin", password="")

        sent_commands = []
        child.sendline.side_effect = lambda cmd: sent_commands.append(cmd)

        with patch.object(n, '_connect', return_value=True):
            n.child = child
            n.mylog = io.BytesIO(b"output")
            with patch.object(n, '_logclean', return_value="output"):
                n.run(["show ip route {subnet}"], vars={"subnet": "10.0.0.0/24"})

        assert "show ip route 10.0.0.0/24" in sent_commands

    def test_run_saves_to_folder(self, mock_pexpect, tmp_path):
        """folder param saves log file."""
        child = mock_pexpect["child"]
        child.expect.return_value = 9

        from connpy.core import node
        n = node("router1", "10.0.0.1", user="admin", password="")

        with patch.object(n, '_connect', return_value=True):
            n.child = child
            n.mylog = io.BytesIO(b"log output")
            with patch.object(n, '_logclean', return_value="log output"):
                n.run(["show version"], folder=str(tmp_path))

        log_files = list(tmp_path.glob("router1_*.txt"))
        assert len(log_files) == 1
        assert "log output" in log_files[0].read_text()


class TestNodeTest:
    def test_test_returns_dict(self, mock_pexpect):
        """test() returns dict of results."""
        child = mock_pexpect["child"]
        child.expect.return_value = 0  # prompt found (index 0 in test expects)

        from connpy.core import node
        n = node("router1", "10.0.0.1", user="admin", password="")

        with patch.object(n, '_connect', return_value=True):
            n.child = child
            n.mylog = io.BytesIO(b"1.1.1.1 is up")
            with patch.object(n, '_logclean', return_value="1.1.1.1 is up"):
                result = n.test(["ping 1.1.1.1"], "1.1.1.1")

        assert isinstance(result, dict)
        assert result.get("1.1.1.1") == True

    def test_test_expected_not_found(self, mock_pexpect):
        """Expected text not found returns False."""
        child = mock_pexpect["child"]
        child.expect.return_value = 0

        from connpy.core import node
        n = node("router1", "10.0.0.1", user="admin", password="")

        with patch.object(n, '_connect', return_value=True):
            n.child = child
            n.mylog = io.BytesIO(b"some other output")
            with patch.object(n, '_logclean', return_value="some other output"):
                result = n.test(["ping 1.1.1.1"], "1.1.1.1")

        assert isinstance(result, dict)
        assert result.get("1.1.1.1") == False


# =========================================================================
# nodes (parallel) tests
# =========================================================================

class TestNodes:
    def test_nodes_init(self):
        """Creates list of node objects."""
        from connpy.core import nodes
        nodes_dict = {
            "r1": {"host": "10.0.0.1", "user": "admin", "password": ""},
            "r2": {"host": "10.0.0.2", "user": "admin", "password": ""}
        }
        mynodes = nodes(nodes_dict)
        assert len(mynodes.nodelist) == 2
        assert hasattr(mynodes, "r1")
        assert hasattr(mynodes, "r2")

    def test_nodes_run_parallel(self):
        """run() executes on all nodes and returns dict."""
        from connpy.core import nodes

        nodes_dict = {
            "r1": {"host": "10.0.0.1", "user": "admin", "password": ""},
            "r2": {"host": "10.0.0.2", "user": "admin", "password": ""}
        }
        mynodes = nodes(nodes_dict)

        # Mock run on each node — must set output AND status on the node
        for n in mynodes.nodelist:
            original_node = n  # capture by value
            def make_mock(node_ref):
                def mock_run(commands, **kwargs):
                    node_ref.output = f"output from {node_ref.unique}"
                    node_ref.status = 0
                return mock_run
            n.run = make_mock(n)

        result = mynodes.run(["show version"])
        assert "r1" in result
        assert "r2" in result

    def test_nodes_splitlist(self):
        """_splitlist divides list correctly."""
        from connpy.core import nodes
        mynodes = nodes({"r1": {"host": "1.1.1.1", "user": "", "password": ""}})
        chunks = list(mynodes._splitlist([1, 2, 3, 4, 5], 2))
        assert chunks == [[1, 2], [3, 4], [5]]

    def test_nodes_run_with_vars(self):
        """Variables per node and __global__ work."""
        from connpy.core import nodes

        nodes_dict = {
            "r1": {"host": "10.0.0.1", "user": "admin", "password": ""},
        }
        mynodes = nodes(nodes_dict)

        captured_vars = {}

        def mock_run(commands, vars=None, **kwargs):
            captured_vars.update(vars or {})
            mynodes.r1.output = "ok"
            mynodes.r1.status = 0

        mynodes.r1.run = mock_run

        variables = {
            "__global__": {"mask": "255.255.255.0"},
            "r1": {"ip": "10.0.0.1"}
        }
        mynodes.run(["show ip"], vars=variables)
        assert captured_vars.get("mask") == "255.255.255.0"
        assert captured_vars.get("ip") == "10.0.0.1"

    def test_nodes_on_complete_callback(self):
        """on_complete callback fires per node."""
        from connpy.core import nodes

        nodes_dict = {
            "r1": {"host": "10.0.0.1", "user": "admin", "password": ""},
        }
        mynodes = nodes(nodes_dict)

        completed = []

        def mock_run(commands, **kwargs):
            mynodes.r1.output = "done"
            mynodes.r1.status = 0

        mynodes.r1.run = mock_run

        def on_done(unique, output, status):
            completed.append(unique)

        mynodes.run(["show version"], on_complete=on_done)
        assert "r1" in completed
