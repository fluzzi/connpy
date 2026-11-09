"""Tests for YAML safe deserialization and connection options sanitization with allow_unsafe_options."""

from unittest.mock import MagicMock, patch

import pytest

from connpy.cli.run_handler import RunHandler
from connpy.core import node
from connpy.grpc_layer.server import ImportExportServicer
from connpy.services.exceptions import InvalidConfigurationError
from connpy.services.import_export_service import ImportExportService

# =========================================================================
# 1. Tests de Deserialización YAML Segura
# =========================================================================


class TestYamlSafeDeserialization:
    def test_import_export_service_rejects_unsafe_yaml(self, config, tmp_path):
        """YAML with !!python/object/apply must fail with InvalidConfigurationError and never execute."""
        malicious_yaml = tmp_path / "malicious.yaml"
        malicious_yaml.write_text('!!python/object/apply:os.system ["echo pwned"]')

        service = ImportExportService(config)

        with patch("os.system") as mock_system:
            with pytest.raises(InvalidConfigurationError):
                service.import_from_file(str(malicious_yaml))
            mock_system.assert_not_called()

    def test_grpc_import_from_file_rejects_unsafe_yaml(self):
        """gRPC import with !!python/object/apply must abort via context and never execute."""
        payload = '---YAML---\n!!python/object/apply:os.system ["echo pwned"]'
        request = MagicMock()
        request.value = payload
        context = MagicMock()

        provider = MagicMock()
        servicer = ImportExportServicer(provider)

        with patch("os.system") as mock_system:
            servicer.import_from_file(request, context)
            context.abort.assert_called_once()
            mock_system.assert_not_called()

    def test_run_handler_yaml_run_rejects_unsafe_yaml(self, tmp_path):
        """RunHandler.yaml_run with !!python/object/apply exits cleanly with code 10 and never executes."""
        malicious_playbook = tmp_path / "bad_playbook.yaml"
        malicious_playbook.write_text(
            'tasks:\n  - name: exploit\n    action: run\n    nodes: all\n    commands:\n      - !!python/object/apply:os.system ["echo pwned"]\n'
        )

        app = MagicMock()
        handler = RunHandler(app)
        args = MagicMock()
        args.data = [str(malicious_playbook)]
        args.preflight_ai = False
        args.analyze = None

        with patch("os.system") as mock_system:
            with pytest.raises(SystemExit) as exc_info:
                handler.yaml_run(args)
            assert exc_info.value.code == 10
            mock_system.assert_not_called()

    def test_run_handler_yaml_run_rejects_non_dict_yaml(self, tmp_path):
        """RunHandler.yaml_run with empty or non-dict YAML exits cleanly with code 10."""
        non_dict_playbook = tmp_path / "list_playbook.yaml"
        non_dict_playbook.write_text("- item1\n- item2\n")

        app = MagicMock()
        handler = RunHandler(app)
        args = MagicMock()
        args.data = [str(non_dict_playbook)]
        args.preflight_ai = False
        args.analyze = None

        with pytest.raises(SystemExit) as exc_info:
            handler.yaml_run(args)
        assert exc_info.value.code == 10


# =========================================================================
# 2. Tests Negativos de Inyección en Protocolos (allow_unsafe_options=False)
# =========================================================================


class TestProtocolInjectionNegative:
    @pytest.mark.parametrize("proto", ["ssh", "sftp", "telnet", "kubectl", "docker", "ssm"])
    def test_host_starting_with_dash_raises_value_error(self, proto):
        """Host starting with '-' raises ValueError across all protocols."""
        n = node("bad_node", "-oProxyCommand=id", protocol=proto)
        with pytest.raises(ValueError, match="Host cannot start with '-'"):
            n._get_cmd()

    @pytest.mark.parametrize("proto", ["ssh", "sftp", "telnet", "kubectl", "docker", "ssm"])
    def test_host_with_unsafe_characters_raises_value_error(self, proto):
        """Host containing whitespace, control characters or shell metacharacters raises ValueError."""
        for bad_host in ["host; id", "host | id", "host`id`", "host$id", "host&id"]:
            n = node("bad_node", bad_host, protocol=proto)
            with pytest.raises(ValueError, match="Invalid characters in host"):
                n._get_cmd()

    @pytest.mark.parametrize("proto", ["ssh", "sftp", "telnet", "kubectl", "docker", "ssm"])
    def test_user_starting_with_dash_raises_value_error(self, proto):
        """User starting with '-' raises ValueError across all protocols."""
        n = node("n1", "10.0.0.1", user="-oProxyCommand=id", protocol=proto)
        with pytest.raises(ValueError, match="User cannot start with '-'"):
            n._get_cmd()

    @pytest.mark.parametrize("proto", ["ssh", "sftp", "telnet", "kubectl", "docker", "ssm"])
    def test_user_with_unsafe_characters_raises_value_error(self, proto):
        """User containing shell metacharacters raises ValueError across all protocols."""
        for bad_user in ["; id", "user | id", "user`id`", "user$id", "user&id"]:
            n = node("n1", "10.0.0.1", user=bad_user, protocol=proto)
            with pytest.raises(ValueError, match="Invalid characters in user"):
                n._get_cmd()

    @pytest.mark.parametrize("proto", ["ssh", "sftp", "telnet"])
    def test_invalid_port_raises_value_error(self, proto):
        """Invalid port in SSH/SFTP/Telnet raises ValueError."""
        for bad_port in ["22 -oProxyCommand=id", "99999", "abc", "0", "-1"]:
            n = node("n1", "10.0.0.1", port=bad_port, protocol=proto)
            with pytest.raises(ValueError, match="Invalid TCP SSH port"):
                n._get_cmd()

    @pytest.mark.parametrize("proto", ["ssh", "sftp"])
    def test_dangerous_ssh_directives_in_options_raise_value_error(self, proto):
        """Directives like ProxyCommand, LocalCommand, etc. in options raise ValueError."""
        dangerous_opts = [
            '-o ProxyCommand="sh -c id"',
            "-oProxyCommand=id",
            '-o LocalCommand="id"',
            "-o PermitLocalCommand=yes",
            '-o KnownHostsCommand="id"',
            '-o "ProxyCommand=id"',
            "-o proxycommand=id",
        ]
        for opt in dangerous_opts:
            n = node("n1", "10.0.0.1", options=opt, protocol=proto)
            with pytest.raises(ValueError, match="Disallowed SSH directive in options"):
                n._get_cmd()

    @pytest.mark.parametrize("proto", ["ssh", "sftp", "telnet", "kubectl", "docker", "ssm"])
    def test_unbalanced_quotes_in_options_raise_value_error(self, proto):
        """Unbalanced quotes in options raise ValueError."""
        n = node("n1", "10.0.0.1", options='-o "StrictHostKeyChecking=no', protocol=proto)
        with pytest.raises(ValueError, match="unbalanced quotes"):
            n._get_cmd()

    def test_ssm_invalid_region_or_profile_tags_raise_value_error(self):
        """SSM tags 'region' and 'profile' reject leading dash and shell metacharacters."""
        n_reg = node("ssm1", "i-1234567890abcdef0", protocol="ssm", tags={"region": "us-east-1; id"})
        with pytest.raises(ValueError, match="Invalid characters in SSM tag"):
            n_reg._get_cmd()

        n_prof = node("ssm2", "i-1234567890abcdef0", protocol="ssm", tags={"profile": "-bad"})
        with pytest.raises(ValueError, match="cannot start with '-'"):
            n_prof._get_cmd()

    def test_container_command_validation(self):
        """Kube and Docker commands reject newlines and unbalanced quotes."""
        n_kube = node("k1", "pod-1", protocol="kubectl", tags={"kube_command": 'sh -c "echo\nid"'})
        with pytest.raises(ValueError, match="cannot contain newlines or null bytes"):
            n_kube._get_cmd()

        n_kube_q = node("k2", "pod-1", protocol="kubectl", tags={"kube_command": 'sh -c "unclosed'})
        with pytest.raises(ValueError, match="unbalanced quotes"):
            n_kube_q._get_cmd()

        n_doc = node("d1", "cont-1", protocol="docker", tags={"docker_command": 'sh -c "echo\nid"'})
        with pytest.raises(ValueError, match="cannot contain newlines or null bytes"):
            n_doc._get_cmd()

        n_doc_q = node("d2", "cont-1", protocol="docker", tags={"docker_command": 'sh -c "unclosed'})
        with pytest.raises(ValueError, match="unbalanced quotes"):
            n_doc_q._get_cmd()


# =========================================================================
# 3. Tests Positivos de Regresión y allow_unsafe_options=True
# =========================================================================


class TestPositiveAndAllowUnsafeOptions:
    def test_ssh_jumphost_proxycommand_allowed_when_safe_mode(self, populated_config):
        """Valid jumphost chain still generates -o ProxyCommand correctly when allow_unsafe_options=False."""
        # populated_config has 'router1'
        n = node("target", "10.0.0.2", protocol="ssh", jumphost="router1", config=populated_config)
        cmd = n._get_cmd()
        assert '-o ProxyCommand="' in cmd
        assert "ssh -W %h:%p" in cmd
        assert "admin@10.0.0.1" in cmd

    def test_ssh_safe_options_permitted(self):
        """Benign SSH options work without issue."""
        safe_opts = "-o StrictHostKeyChecking=no -i ~/.ssh/id_rsa -L 8080:localhost:80"
        n = node("s1", "10.0.0.1", protocol="ssh", options=safe_opts)
        cmd = n._get_cmd()
        assert safe_opts in cmd

    def test_user_domain_and_ipv6_bracketed_permitted(self):
        """Formats like DOMAIN\\user and bracketed IPv6 [2001:db8::1] remain supported."""
        n = node("s1", "[2001:db8::1]", user=r"DOMAIN\user", protocol="ssh", port="22")
        cmd = n._get_cmd()
        assert r"DOMAIN\user@[2001:db8::1]" in cmd
        assert "-p 22" in cmd

    def test_allow_unsafe_options_via_config(self, populated_config):
        """When config.config['allow_unsafe_options'] is True, ProxyCommand in options is allowed."""
        populated_config.config["allow_unsafe_options"] = True
        custom_proxy = '-o ProxyCommand="ssh -W %h:%p bastion"'
        n = node("s1", "10.0.0.1", protocol="ssh", options=custom_proxy, config=populated_config)
        cmd = n._get_cmd()
        assert custom_proxy in cmd

    def test_allow_unsafe_options_via_kwarg(self):
        """When allow_unsafe_options=True passed to node, dangerous options are allowed."""
        custom_proxy = '-o ProxyCommand="ssh -W %h:%p bastion"'
        n = node("s1", "10.0.0.1", protocol="ssh", options=custom_proxy, allow_unsafe_options=True)
        cmd = n._get_cmd()
        assert custom_proxy in cmd
