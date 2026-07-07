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

    def test_test_expected_regex(self, mock_pexpect):
        """Regex in expected matches correctly."""
        child = mock_pexpect["child"]
        child.expect.return_value = 0

        from connpy.core import node
        n = node("router1", "10.0.0.1", user="admin", password="")

        with patch.object(n, '_connect', return_value=True):
            n.child = child
            n.mylog = io.BytesIO(b"Debian version 12.5")
            with patch.object(n, '_logclean', return_value="Debian version 12.5"):
                result = n.test(["cat /etc/debian_version"], "version \\d+\\.\\d+")

        assert isinstance(result, dict)
        assert result.get("version \\d+\\.\\d+") == True

    def test_test_expected_invalid_regex(self, mock_pexpect):
        """Malformed regex defaults to literal matching safely."""
        child = mock_pexpect["child"]
        child.expect.return_value = 0

        from connpy.core import node
        n = node("router1", "10.0.0.1", user="admin", password="")

        with patch.object(n, '_connect', return_value=True):
            n.child = child
            # (invalid is a malformed regex (missing closing paren), but matches literally
            n.mylog = io.BytesIO(b"some (invalid text")
            with patch.object(n, '_logclean', return_value="some (invalid text"):
                result = n.test(["echo"], "(invalid")

        assert isinstance(result, dict)
        assert result.get("(invalid") == True

    def test_test_expected_with_vars(self, mock_pexpect):
        """Expected output formats variables properly."""
        child = mock_pexpect["child"]
        child.expect.return_value = 0

        from connpy.core import node
        n = node("router1", "10.0.0.1", user="admin", password="")

        with patch.object(n, '_connect', return_value=True):
            n.child = child
            n.mylog = io.BytesIO(b"Debian version 12")
            with patch.object(n, '_logclean', return_value="Debian version 12"):
                result = n.test(["echo"], "version {version_num}", vars={"version_num": "12"})

        assert isinstance(result, dict)
        assert result.get("version 12") == True


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


# =========================================================================
# Jumphost chain tests
# =========================================================================

class TestJumphostChain:
    """Tests for chained jumphost (multi-hop ProxyCommand) support."""

    def _make_config_with_nodes(self, tmp_config_dir, nodes, profiles=None):
        """Helper to create a config with custom nodes."""
        import yaml
        from connpy.configfile import configfile
        if profiles is None:
            profiles = {
                "default": {
                    "host": "", "protocol": "ssh", "port": "", "user": "",
                    "password": "", "options": "", "logs": "", "tags": "", "jumphost": ""
                }
            }
        data = {
            "config": {"case": False, "idletime": 30, "fzf": False},
            "connections": nodes,
            "profiles": profiles
        }
        config_file = tmp_config_dir / "config.yaml"
        config_file.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        import os
        os.chmod(str(config_file), 0o600)
        return configfile(conf=str(config_file), key=str(tmp_config_dir / ".osk"))

    def test_single_jumphost(self, tmp_config_dir):
        """Regression: single jumphost produces correct ProxyCommand."""
        from connpy.core import node
        nodes = {
            "bastion": {
                "host": "10.0.0.1", "protocol": "ssh", "port": "2222",
                "user": "admin", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "", "type": "connection"
            },
            "dest": {
                "host": "10.0.1.1", "protocol": "ssh", "port": "",
                "user": "root", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "bastion", "type": "connection"
            }
        }
        config = self._make_config_with_nodes(tmp_config_dir, nodes)
        n = node("dest", "10.0.1.1", user="root", jumphost="bastion", config=config)
        assert 'ProxyCommand=' in n.jumphost
        assert '-W %h:%p' in n.jumphost
        assert '-p 2222' in n.jumphost
        assert 'admin@10.0.0.1' in n.jumphost

    def test_two_hop_chain(self, tmp_config_dir):
        """Two-hop SSH chain: dest -> bastionA -> bastionB."""
        from connpy.core import node
        nodes = {
            "bastionB": {
                "host": "10.0.0.1", "protocol": "ssh", "port": "",
                "user": "userB", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "", "type": "connection"
            },
            "bastionA": {
                "host": "10.0.0.2", "protocol": "ssh", "port": "",
                "user": "userA", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "bastionB", "type": "connection"
            },
            "dest": {
                "host": "10.0.1.1", "protocol": "ssh", "port": "",
                "user": "root", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "bastionA", "type": "connection"
            }
        }
        config = self._make_config_with_nodes(tmp_config_dir, nodes)
        n = node("dest", "10.0.1.1", user="root", jumphost="bastionA", config=config)
        # Should contain nested ProxyCommand
        assert 'ProxyCommand=' in n.jumphost
        assert 'userA@10.0.0.2' in n.jumphost
        assert 'userB@10.0.0.1' in n.jumphost
        # Inner proxy should be escaped
        assert 'ProxyCommand=\\"' in n.jumphost or 'ProxyCommand=\\\\' in n.jumphost

    def test_three_hop_chain(self, tmp_config_dir):
        """Three-hop SSH chain: dest -> A -> B -> C."""
        from connpy.core import node
        nodes = {
            "hopC": {
                "host": "10.0.0.3", "protocol": "ssh", "port": "",
                "user": "uc", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "", "type": "connection"
            },
            "hopB": {
                "host": "10.0.0.2", "protocol": "ssh", "port": "",
                "user": "ub", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "hopC", "type": "connection"
            },
            "hopA": {
                "host": "10.0.0.1", "protocol": "ssh", "port": "",
                "user": "ua", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "hopB", "type": "connection"
            },
            "dest": {
                "host": "10.0.1.1", "protocol": "ssh", "port": "",
                "user": "root", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "hopA", "type": "connection"
            }
        }
        config = self._make_config_with_nodes(tmp_config_dir, nodes)
        n = node("dest", "10.0.1.1", user="root", jumphost="hopA", config=config)
        # All three hosts should appear in the command
        assert 'uc@10.0.0.3' in n.jumphost
        assert 'ub@10.0.0.2' in n.jumphost
        assert 'ua@10.0.0.1' in n.jumphost

    def test_circular_detection(self, tmp_config_dir):
        """Circular jumphost reference raises ValueError."""
        from connpy.core import node
        nodes = {
            "hopA": {
                "host": "10.0.0.1", "protocol": "ssh", "port": "",
                "user": "", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "hopB", "type": "connection"
            },
            "hopB": {
                "host": "10.0.0.2", "protocol": "ssh", "port": "",
                "user": "", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "hopA", "type": "connection"
            },
            "dest": {
                "host": "10.0.1.1", "protocol": "ssh", "port": "",
                "user": "", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "hopA", "type": "connection"
            }
        }
        config = self._make_config_with_nodes(tmp_config_dir, nodes)
        with pytest.raises(ValueError, match="Circular jumphost reference"):
            node("dest", "10.0.1.1", jumphost="hopA", config=config)

    def test_max_depth(self, tmp_config_dir):
        """Chain exceeding 5 hops raises ValueError."""
        from connpy.core import node
        nodes = {}
        # Build chain of 6 hops: hop0 -> hop1 -> ... -> hop5
        for i in range(6):
            jh = f"hop{i+1}" if i < 5 else ""
            nodes[f"hop{i}"] = {
                "host": f"10.0.0.{i}", "protocol": "ssh", "port": "",
                "user": "", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": jh, "type": "connection"
            }
        nodes["dest"] = {
            "host": "10.0.1.1", "protocol": "ssh", "port": "",
            "user": "", "password": "", "options": "",
            "logs": "", "tags": "", "jumphost": "hop0", "type": "connection"
        }
        config = self._make_config_with_nodes(tmp_config_dir, nodes)
        with pytest.raises(ValueError, match="maximum depth of 5"):
            node("dest", "10.0.1.1", jumphost="hop0", config=config)

    def test_kubectl_with_jumphost_error(self, tmp_config_dir):
        """kubectl jumphost with its own jumphost raises ValueError."""
        from connpy.core import node
        nodes = {
            "sshhost": {
                "host": "10.0.0.1", "protocol": "ssh", "port": "",
                "user": "", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "", "type": "connection"
            },
            "kubejump": {
                "host": "my-pod", "protocol": "kubectl", "port": "",
                "user": "", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "sshhost", "type": "connection"
            },
            "dest": {
                "host": "10.0.1.1", "protocol": "ssh", "port": "",
                "user": "", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "kubejump", "type": "connection"
            }
        }
        config = self._make_config_with_nodes(tmp_config_dir, nodes)
        with pytest.raises(ValueError, match="does not support chained jumphosts"):
            node("dest", "10.0.1.1", jumphost="kubejump", config=config)

    def test_password_chain_order(self, tmp_config_dir):
        """Passwords are collected innermost-first: B, A, dest."""
        from connpy.core import node
        nodes = {
            "bastionB": {
                "host": "10.0.0.1", "protocol": "ssh", "port": "",
                "user": "ub", "password": "passB", "options": "",
                "logs": "", "tags": "", "jumphost": "", "type": "connection"
            },
            "bastionA": {
                "host": "10.0.0.2", "protocol": "ssh", "port": "",
                "user": "ua", "password": "passA", "options": "",
                "logs": "", "tags": "", "jumphost": "bastionB", "type": "connection"
            },
            "dest": {
                "host": "10.0.1.1", "protocol": "ssh", "port": "",
                "user": "root", "password": "passDest", "options": "",
                "logs": "", "tags": "", "jumphost": "bastionA", "type": "connection"
            }
        }
        config = self._make_config_with_nodes(tmp_config_dir, nodes)
        n = node("dest", "10.0.1.1", user="root", password="passDest",
                 jumphost="bastionA", config=config)
        # Order: innermost (B) -> outer (A) -> destination
        assert n.password == ["passB", "passA", "passDest"]

    def test_chain_with_options_and_port(self, tmp_config_dir):
        """Options and ports are preserved for each hop in the chain."""
        from connpy.core import node
        nodes = {
            "bastionB": {
                "host": "10.0.0.1", "protocol": "ssh", "port": "2222",
                "user": "ub", "password": "", "options": "-o StrictHostKeyChecking=no",
                "logs": "", "tags": "", "jumphost": "", "type": "connection"
            },
            "bastionA": {
                "host": "10.0.0.2", "protocol": "ssh", "port": "3333",
                "user": "ua", "password": "", "options": "-i /tmp/key.pem",
                "logs": "", "tags": "", "jumphost": "bastionB", "type": "connection"
            },
            "dest": {
                "host": "10.0.1.1", "protocol": "ssh", "port": "",
                "user": "root", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "bastionA", "type": "connection"
            }
        }
        config = self._make_config_with_nodes(tmp_config_dir, nodes)
        n = node("dest", "10.0.1.1", user="root", jumphost="bastionA", config=config)
        assert '-p 2222' in n.jumphost
        assert '-p 3333' in n.jumphost
        assert 'StrictHostKeyChecking=no' in n.jumphost
        assert '/tmp/key.pem' in n.jumphost

    def test_chain_with_ssm_inner(self, tmp_config_dir):
        """SSH outer jumphost with SSM inner jumphost works."""
        from connpy.core import node
        nodes = {
            "ssm_bastion": {
                "host": "i-12345", "protocol": "ssm", "port": "",
                "user": "ec2-user", "password": "", "options": "",
                "logs": "", "tags": {"region": "us-east-1"}, "jumphost": "", "type": "connection"
            },
            "ssh_jump": {
                "host": "10.0.0.2", "protocol": "ssh", "port": "",
                "user": "admin", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "ssm_bastion", "type": "connection"
            },
            "dest": {
                "host": "10.0.1.1", "protocol": "ssh", "port": "",
                "user": "root", "password": "", "options": "",
                "logs": "", "tags": "", "jumphost": "ssh_jump", "type": "connection"
            }
        }
        config = self._make_config_with_nodes(tmp_config_dir, nodes)
        n = node("dest", "10.0.1.1", user="root", jumphost="ssh_jump", config=config)
        assert 'aws ssm start-session' in n.jumphost
        assert 'admin@10.0.0.2' in n.jumphost
        assert '--region us-east-1' in n.jumphost
