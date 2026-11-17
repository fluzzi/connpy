"""Tests for adaptive multi-hop timeouts, progress extensions, deadline, and hardened expects."""

import itertools
import os
import re
import shlex
from unittest.mock import MagicMock, patch

import pexpect
import pytest
import yaml

from connpy.configfile import configfile
from connpy.core import (
    DEFAULT_ADAPTIVE_BASE_TIMEOUT,
    DEFAULT_AUTH_TIMEOUT,
    DEFAULT_DIRECT_CONNECT_TIMEOUT,
    DEFAULT_MAX_PROGRESS_EXTENSIONS,
    DEFAULT_MAX_TOTAL_CONNECT_SECONDS,
    DEFAULT_MOSH_CONNECT_TIMEOUT,
    DEFAULT_OTHER_HOP_COST,
    DEFAULT_SSM_DIRECT_COST,
    DEFAULT_SSM_HOP_COST,
    MAX_PRE_AUTH_BUFFER_BYTES,
    MAX_STAGE_TIMEOUT_CAP,
    compile_expects,
    node,
    nodes,
    resolve_timeouts,
    validate_strict_timeout_int,
)
from connpy.services.exceptions import ConnpyError
from connpy.services.execution_service import ExecutionService


class FakeClock:
    """Deterministic monotonic clock helper for unit tests."""

    def __init__(self, initial: float = 0.0):
        self.now = initial

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


_CONF_COUNTER = itertools.count(1)


def _make_config(tmp_path, connections, profiles=None):
    if profiles is None:
        profiles = {
            "default": {
                "host": "",
                "protocol": "ssh",
                "port": "",
                "user": "",
                "password": "",
                "options": "",
                "logs": "",
                "tags": "",
                "jumphost": "",
            }
        }
    conf_dir = tmp_path / f"conf_{next(_CONF_COUNTER)}"
    conf_dir.mkdir(parents=True, exist_ok=True)
    conf_file = conf_dir / "config.yaml"
    data = {
        "config": {"case": False, "idletime": 30, "fzf": False},
        "connections": connections,
        "profiles": profiles,
    }
    conf_file.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    os.chmod(str(conf_file), 0o600)
    return configfile(conf=str(conf_file), key=str(conf_dir / ".osk"))


def _make_ssm_jh_router_config(tmp_path, *, ssm_host="i-0123456789abcdef0", ssm_tags=None, ssh_options=""):
    connections = {
        "ssm_bastion": {
            "host": ssm_host,
            "protocol": "ssm",
            "port": "",
            "user": "ec2-user",
            "password": "",
            "options": "",
            "logs": "",
            "tags": ssm_tags if ssm_tags is not None else {"profile": "prod", "region": "us-east-1"},
            "jumphost": "",
            "type": "connection",
        },
        "jh_ssh": {
            "host": "10.10.0.2",
            "protocol": "ssh",
            "port": "2222",
            "user": "jumpuser",
            "password": "",
            "options": ssh_options,
            "logs": "",
            "tags": "",
            "jumphost": "ssm_bastion",
            "type": "connection",
        },
        "router1": {
            "host": "10.20.0.5",
            "protocol": "ssh",
            "port": "",
            "user": "netadmin",
            "password": "",
            "options": "",
            "logs": "",
            "tags": "",
            "jumphost": "jh_ssh",
            "type": "connection",
        },
    }
    return _make_config(tmp_path, connections)


# =============================================================================
# 1. validate_strict_timeout_int and resolve_timeouts
# =============================================================================


class TestResolveTimeouts:
    def test_constants_values(self):
        assert DEFAULT_DIRECT_CONNECT_TIMEOUT == 10
        assert DEFAULT_MOSH_CONNECT_TIMEOUT == 15
        assert DEFAULT_ADAPTIVE_BASE_TIMEOUT == 15
        assert DEFAULT_SSM_DIRECT_COST == 20
        assert DEFAULT_SSM_HOP_COST == 25
        assert DEFAULT_OTHER_HOP_COST == 10
        assert DEFAULT_AUTH_TIMEOUT == 25
        assert DEFAULT_MAX_PROGRESS_EXTENSIONS == 3
        assert MAX_STAGE_TIMEOUT_CAP == 600
        assert DEFAULT_MAX_TOTAL_CONNECT_SECONDS == 180
        assert MAX_PRE_AUTH_BUFFER_BYTES == 65536

    def test_direct_ssh_defaults_and_legacy_override(self):
        assert resolve_timeouts("ssh", {}, []) == (10, 25, 10, 3, 180)
        assert resolve_timeouts("ssh", {}, [], _default_cmd_timeout=20) == (10, 25, 20, 3, 180)
        assert resolve_timeouts("ssh", {}, [], timeout=5) == (5, 5, 5, 3, 180)

    def test_direct_mosh_and_ssm_budgets(self):
        assert resolve_timeouts("mosh", {}, [], timeout=5) == (15, 5, 5, 3, 180)
        assert resolve_timeouts("mosh", {}, []) == (15, 25, 10, 3, 180)
        assert resolve_timeouts("mosh", {}, [], timeout=30) == (30, 30, 30, 3, 180)

        assert resolve_timeouts("ssm", {}, []) == (20, 25, 10, 3, 180)
        assert resolve_timeouts("ssm", {}, [], timeout=5) == (5, 5, 5, 3, 180)
        assert resolve_timeouts("ssm", {}, [], timeout=45) == (45, 45, 45, 3, 180)

    def test_multihop_ssm_jh_router_and_short_overrides(self):
        hop_meta = [
            {"name": "ssm_bastion", "protocol": "ssm"},
            {"name": "jh_ssh", "protocol": "ssh"},
        ]
        # 15 + 25 + 10 = 50 connect timeout; 25 auth; 10 or 20 cmd
        assert resolve_timeouts("ssh", {}, hop_meta) == (50, 25, 10, 3, 180)
        assert resolve_timeouts("ssh", {}, hop_meta, _default_cmd_timeout=20) == (50, 25, 20, 3, 180)
        assert resolve_timeouts("ssh", {}, hop_meta, timeout=75) == (75, 75, 75, 3, 180)
        assert resolve_timeouts(
            "ssh",
            {},
            hop_meta,
            timeout=75,
            connect_timeout=12,
            auth_timeout=8,
        ) == (12, 8, 75, 3, 180)
        assert resolve_timeouts("ssh", {}, hop_meta, connect_timeout=12) == (12, 25, 10, 3, 180)

    def test_max_total_ceiling_acts_as_upper_bound(self):
        assert resolve_timeouts("ssh", {}, [], _default_max_total_seconds=45) == (10, 25, 10, 3, 45)
        assert resolve_timeouts("ssh", {}, [], _default_max_total_seconds=500) == (10, 25, 10, 3, 180)

    @pytest.mark.parametrize("bad_val", [True, False, 10.5, "10", 0, -5, 601])
    def test_rejects_non_strict_int_values(self, bad_val):
        with pytest.raises(ValueError, match="Invalid"):
            validate_strict_timeout_int(bad_val, "timeout")
        with pytest.raises(ValueError, match="Invalid"):
            resolve_timeouts("ssh", {}, [], timeout=bad_val)
        with pytest.raises(ValueError, match="Invalid"):
            resolve_timeouts("ssh", {}, [], connect_timeout=bad_val)
        with pytest.raises(ValueError, match="Invalid"):
            resolve_timeouts("ssh", {}, [], auth_timeout=bad_val)

    def test_max_progress_extensions_capped_at_10(self):
        assert resolve_timeouts("ssh", {"max_progress_extensions": 0}, []) == (10, 25, 10, 0, 180)
        assert resolve_timeouts("ssh", {"max_progress_extensions": 10}, []) == (10, 25, 10, 10, 180)
        with pytest.raises(ValueError, match="Invalid"):
            resolve_timeouts("ssh", {"max_progress_extensions": 11}, [])

    def test_connect_and_run_return_code_12_on_invalid_timeout(self):
        n = node("r1", "10.0.0.1", user="admin", protocol="ssh")
        res_conn = n._connect(timeout=True)
        assert res_conn.startswith("Connection failed code: 12\nInvalid timeout configuration:")

        res_run = n.run(["show version"], connect_timeout="bad")
        assert res_run.startswith("Connection failed code: 12\nInvalid timeout configuration:")
        assert n.status == 1

        res_test = n.test(["show version"], "v1", auth_timeout=False)
        assert res_test.startswith("Connection failed code: 12\nInvalid timeout configuration:")
        assert n.status == 1
        assert n.result is None


# =============================================================================
# 2. ExecutionService local vs remote gRPC
# =============================================================================


class TestExecutionServiceTimeouts:
    def test_local_execution_service_defaults_cmd_timeout_20_and_auth_25(self, tmp_path):
        cfg = _make_config(
            tmp_path,
            {
                "r1": {
                    "host": "10.0.0.1",
                    "protocol": "ssh",
                    "port": "",
                    "user": "admin",
                    "password": "mysecretpassword",
                    "options": "",
                    "logs": "",
                    "tags": "",
                    "jumphost": "",
                    "type": "connection",
                }
            },
        )
        svc = ExecutionService(cfg)

        mock_child = MagicMock()
        # _connect: [8 (Password:), 9 (prompt)], run(): [9 (prompt), 9 (SIGWINCH consume), 9 (after cmd)]
        mock_child.expect.side_effect = [8, 9, 0, 0, 0]
        mock_child.before = b""
        mock_child.after = b"r1#"
        mock_child.readline.return_value = b""
        mock_child.child_fd = 3

        with (
            patch("connpy.core.pexpect.spawn", return_value=mock_child),
            patch("pexpect.fdpexpect.fdspawn", return_value=MagicMock()),
        ):
            res = svc.run_commands("r1", ["show version"])

        assert res["r1"]["status"] == 0
        timeouts_seen = [call.kwargs["timeout"] for call in mock_child.expect.call_args_list]
        # 1) connect stage -> 10, 2) auth stage -> 25, 3) run pre-cmd -> 20, 4) winsize -> 1, 5) run post-cmd -> 20
        assert timeouts_seen == [10, 25, 20, 1, 20]

    def test_local_execution_service_test_commands_propagates_stage_timeouts(self, tmp_path):
        cfg = _make_config(
            tmp_path,
            {
                "r1": {
                    "host": "10.0.0.1",
                    "protocol": "ssh",
                    "port": "",
                    "user": "admin",
                    "password": "",
                    "options": "",
                    "logs": "",
                    "tags": "",
                    "jumphost": "",
                    "type": "connection",
                }
            },
        )
        svc = ExecutionService(cfg)
        with patch("connpy.services.execution_service.Nodes") as mock_nodes:
            mock_exec = mock_nodes.return_value
            mock_exec.test.return_value = {"r1": {"ok": True}}
            svc.test_commands(
                "r1",
                ["show version"],
                ["ok"],
                connect_timeout=18,
                auth_timeout=35,
            )
            _, kwargs = mock_exec.test.call_args
            assert kwargs["timeout"] is None
            assert kwargs["connect_timeout"] == 18
            assert kwargs["auth_timeout"] == 35
            assert kwargs["_default_cmd_timeout"] == 20

    def test_remote_grpc_uses_default_20_and_rejects_stage_overrides(self, tmp_path):
        cfg = _make_config(tmp_path, {})
        cfg.config["service_mode"] = "remote"
        cfg.config["remote_host"] = "127.0.0.1:8048"

        fake_stub = MagicMock()
        fake_stub.run_commands.return_value = {"r1": {"output": "ok", "status": 0}}
        fake_stub.test_commands.return_value = {"r1": {"ok": True}}
        svc = ExecutionService(cfg, remote_stub=fake_stub)

        res_run = svc.run_commands("r1", ["show version"])
        assert res_run == {"r1": {"output": "ok", "status": 0}}
        assert fake_stub.run_commands.call_args.kwargs["timeout"] == 20

        res_test = svc.test_commands("r1", ["show version"], ["ok"], timeout=45)
        assert res_test == {"r1": {"ok": True}}
        assert fake_stub.test_commands.call_args.kwargs["timeout"] == 45

        with pytest.raises(ConnpyError, match="not supported in remote gRPC mode"):
            svc.run_commands("r1", ["show version"], connect_timeout=15)

        with pytest.raises(ConnpyError, match="not supported in remote gRPC mode"):
            svc.test_commands("r1", ["show version"], ["ok"], auth_timeout=30)

        with pytest.raises(ConnpyError, match="not supported in remote gRPC mode"):
            svc._ensure_backend_supports_stage_timeouts(connect_timeout=12, auth_timeout=None)


# =============================================================================
# 3. _build_jumphost_chain & SSM/SSH security
# =============================================================================


class TestJumphostChainAndSecurity:
    def test_default_2_tuple_and_include_meta_3_tuple_all_branches(self, tmp_path):
        connections = {
            "ssm_hop": {
                "host": "i-0123456789abcdef0",
                "protocol": "ssm",
                "port": "",
                "user": "ec2-user",
                "password": "",
                "options": "",
                "logs": "",
                "tags": {"profile": "prod", "region": "us-east-1"},
                "jumphost": "",
                "type": "connection",
            },
            "ssh_hop": {
                "host": "10.0.0.2",
                "protocol": "ssh",
                "port": "2222",
                "user": "DOMAIN\\user",
                "password": "",
                "options": "-o StrictHostKeyChecking=no",
                "logs": "",
                "tags": "",
                "jumphost": "ssm_hop",
                "type": "connection",
            },
            "mosh_hop": {
                "host": "10.0.0.3",
                "protocol": "mosh",
                "port": "22",
                "user": "muser",
                "password": "",
                "options": "",
                "logs": "",
                "tags": {"mosh_ssh_options": "-4"},
                "jumphost": "",
                "type": "connection",
            },
            "kube_hop": {
                "host": "jump-pod",
                "protocol": "kubectl",
                "port": "",
                "user": "",
                "password": "",
                "options": "-n ops",
                "logs": "",
                "tags": "",
                "jumphost": "",
                "type": "connection",
            },
            "docker_hop": {
                "host": "jump-ctr",
                "protocol": "docker",
                "port": "",
                "user": "",
                "password": "",
                "options": "",
                "logs": "",
                "tags": "",
                "jumphost": "",
                "type": "connection",
            },
            "unknown_hop": {
                "host": "10.0.0.9",
                "protocol": "serial",
                "port": "",
                "user": "",
                "password": "",
                "options": "",
                "logs": "",
                "tags": "",
                "jumphost": "",
                "type": "connection",
            },
            "router1": {
                "host": "10.20.0.5",
                "protocol": "ssh",
                "port": "",
                "user": "admin",
                "password": "",
                "options": "",
                "logs": "",
                "tags": "",
                "jumphost": "ssh_hop",
                "type": "connection",
            },
        }
        cfg = _make_config(tmp_path, connections)
        r = node("router1", "10.20.0.5", user="admin", jumphost="ssh_hop", config=cfg)

        # Default returns 2-tuple
        res_2 = r._build_jumphost_chain("ssh_hop", cfg)
        assert isinstance(res_2, tuple)
        assert len(res_2) == 2

        # include_meta=True returns 3-tuple for all branches
        raw_cmd, pws, meta = r._build_jumphost_chain("ssh_hop", cfg, include_meta=True)
        assert pws == []
        assert [m["protocol"] for m in meta] == ["ssm", "ssh"]
        assert "--profile prod" in raw_cmd
        assert "--region us-east-1" in raw_cmd
        assert "DOMAIN\\user@10.0.0.2" in raw_cmd

        for jh_name, expected_proto in [
            ("mosh_hop", "mosh"),
            ("kube_hop", "kubectl"),
            ("docker_hop", "docker"),
        ]:
            cmd_b, _, meta_b = r._build_jumphost_chain(jh_name, cfg, include_meta=True)
            assert cmd_b != ""
            assert meta_b == [{"name": jh_name, "protocol": expected_proto}]

        cmd_unk, _, meta_unk = r._build_jumphost_chain("unknown_hop", cfg, include_meta=True)
        assert cmd_unk == ""
        assert meta_unk == []

        # Adaptive timeout derived from router1's _hop_chain_meta
        assert resolve_timeouts(r.protocol, r.tags, r._hop_chain_meta) == (50, 25, 10, 3, 180)

    def test_ssm_target_arn_and_managed_instance_accepted_and_bad_rejected(self, tmp_path):
        for good_target in [
            "i-12345",
            "i-0123abcd",
            "i-0123456789abcdef0",
            "mi-0123456789abcdef0",
            "arn:aws:ssm:us-east-1:123456789012:managed-instance/mi-0123456789abcdef0",
        ]:
            cfg = _make_ssm_jh_router_config(tmp_path, ssm_host=good_target)
            r = node("router1", "10.20.0.5", user="netadmin", jumphost="jh_ssh", config=cfg)
            assert good_target in r.jumphost

        for bad_target in [
            "i-123",
            "i-0123456789abcdef01",
            "-oProxyCommand=evil",
            "i-01234567..89",
            "i-01234567;id",
            "i-01234567\nid",
            "arn:aws:ssm:us-east-1:١٢٣٤٥٦٧٨٩٠١٢:node/1",
        ]:
            cfg = _make_ssm_jh_router_config(tmp_path, ssm_host=bad_target)
            with pytest.raises(ValueError, match="SSM jumphost target|Control characters"):
                node("router1", "10.20.0.5", user="netadmin", jumphost="jh_ssh", config=cfg)

    def test_ssm_profile_region_and_control_char_validation(self, tmp_path):
        for bad_tag in ["../prod", "-flag", "bad profile", "prod;id", "prod\x00"]:
            cfg = _make_ssm_jh_router_config(tmp_path, ssm_tags={"profile": bad_tag})
            with pytest.raises(ValueError, match="SSM jumphost profile|Control characters"):
                node("router1", "10.20.0.5", user="netadmin", jumphost="jh_ssh", config=cfg)

            cfg2 = _make_ssm_jh_router_config(tmp_path, ssm_tags={"region": bad_tag})
            with pytest.raises(ValueError, match="SSM jumphost region|Control characters"):
                node("router1", "10.20.0.5", user="netadmin", jumphost="jh_ssh", config=cfg2)

        # Unconditional control char check even when allow_unsafe_options=True
        cfg_ctrl = _make_config(
            tmp_path,
            {
                "bad_hop": {
                    "host": "10.0.0.1",
                    "protocol": "ssh",
                    "port": "22\x1b",
                    "user": "admin",
                    "password": "",
                    "options": "",
                    "logs": "",
                    "tags": "",
                    "jumphost": "",
                    "type": "connection",
                }
            },
        )
        with pytest.raises(ValueError, match="Control characters"):
            node(
                "r1",
                "10.0.0.2",
                jumphost="bad_hop",
                config=cfg_ctrl,
                allow_unsafe_options=True,
            )

    def test_connect_timeout_and_server_alive_injection_respects_user_options(self, tmp_path):
        # 1) Without user overrides: injects -o ConnectTimeout=50 and -o ServerAliveInterval=15
        cfg = _make_ssm_jh_router_config(tmp_path)
        r = node(
            "router1",
            "10.20.0.5",
            user="netadmin",
            options="-o StrictHostKeyChecking=no",
            jumphost="jh_ssh",
            config=cfg,
        )
        # Default _get_cmd() without connect_timeout does not inject
        assert "ConnectTimeout" not in r._get_cmd()
        cmd_with_ct = r._get_cmd(connect_timeout=50)
        assert "-o ConnectTimeout=50" in cmd_with_ct
        assert "-o ServerAliveInterval=15" in cmd_with_ct

        # 2) With user ConnectTimeout=7 & ServerAliveInterval=20 on SSM and 9/30 on JH: does NOT overwrite
        cfg_user = _make_ssm_jh_router_config(
            tmp_path,
            ssm_tags={
                "profile": "prod",
                "ssh_options": "-o ConnectTimeout=7 -o ServerAliveInterval=20",
            },
            ssh_options="-oConnectTimeout=9 -o ServerAliveInterval=30",
        )
        r2 = node(
            "router1",
            "10.20.0.5",
            user="netadmin",
            options="-o ConnectTimeout=4 -o ServerAliveInterval=60",
            jumphost="jh_ssh",
            config=cfg_user,
        )
        raw_jh, _, _ = r2._build_jumphost_chain("jh_ssh", cfg_user, include_meta=True, connect_timeout=50)
        assert "-oConnectTimeout=9" in raw_jh
        assert "ConnectTimeout=50" not in raw_jh
        assert "-o ServerAliveInterval=30" in raw_jh
        assert "ServerAliveInterval=15" not in raw_jh
        assert "-o ConnectTimeout=7" in raw_jh

        outer_cmd = r2._get_cmd(connect_timeout=50)
        outer_tokens = shlex.split(outer_cmd)
        assert "ConnectTimeout=4" in outer_tokens
        assert "ServerAliveInterval=60" in outer_tokens
        assert "ConnectTimeout=50" not in outer_tokens
        assert "ServerAliveInterval=15" not in outer_tokens

    def test_ssm_ssh_options_rejects_disallowed_directives(self, tmp_path):
        for bad_ssh_opt in ["-o ProxyCommand=evil", "-o LocalCommand=id", "-o ProxyJump=bad"]:
            cfg = _make_ssm_jh_router_config(
                tmp_path,
                ssm_tags={"profile": "prod", "ssh_options": bad_ssh_opt},
            )
            with pytest.raises(ValueError, match="cannot contain|Disallowed"):
                node("router1", "10.20.0.5", user="netadmin", jumphost="jh_ssh", config=cfg)

    def test_kubectl_and_docker_jumphost_options_reject_injection_and_serialize_shlex(self, tmp_path):
        for jh_proto in ("kubectl", "docker"):
            for bad_opts in ["-n ops;id", "--context=$(whoami)", "-n ops|cat", "--flag `id`"]:
                cfg_bad = _make_config(
                    tmp_path,
                    {
                        "jh": {
                            "host": "jump-box",
                            "protocol": jh_proto,
                            "port": "",
                            "user": "",
                            "password": "",
                            "options": bad_opts,
                            "logs": "",
                            "tags": "",
                            "jumphost": "",
                            "type": "connection",
                        },
                        "dest": {
                            "host": "10.20.0.5",
                            "protocol": "ssh",
                            "port": "",
                            "user": "admin",
                            "password": "",
                            "options": "",
                            "logs": "",
                            "tags": "",
                            "jumphost": "jh",
                            "type": "connection",
                        },
                    },
                )
                with pytest.raises(ValueError, match=f"Invalid characters in {jh_proto} options"):
                    node("dest", "10.20.0.5", user="admin", jumphost="jh", config=cfg_bad)

            cfg_ok = _make_config(
                tmp_path,
                {
                    "jh": {
                        "host": "jump-box",
                        "protocol": jh_proto,
                        "port": "",
                        "user": "",
                        "password": "",
                        "options": "-n   ops   --context   prod",
                        "logs": "",
                        "tags": "",
                        "jumphost": "",
                        "type": "connection",
                    },
                    "dest": {
                        "host": "10.20.0.5",
                        "protocol": "ssh",
                        "port": "",
                        "user": "admin",
                        "password": "",
                        "options": "",
                        "logs": "",
                        "tags": "",
                        "jumphost": "jh",
                        "type": "connection",
                    },
                },
            )
            dest = node("dest", "10.20.0.5", user="admin", jumphost="jh", config=cfg_ok)
            raw_cmd, _ = dest._build_jumphost_chain("jh", cfg_ok)
            assert "-n ops --context prod" in raw_cmd


# =============================================================================
# 4. _connect progress extension, global deadline, finally, concurrency, redaction
# =============================================================================


class TestConnectProgressDeadlineAndCleanup:
    def test_progress_extension_with_fake_clock_and_single_warning(self):
        clock = FakeClock(100.0)
        n = node("r1", "10.0.0.1", user="admin", protocol="ssh")

        logs = []
        mock_child = MagicMock()
        mock_child.logfile_read = None
        mock_child.child_fd = 7

        call_count = {"n": 0}

        def expect_side_effect(patterns, timeout):
            call_count["n"] += 1
            idx = call_count["n"]
            clock.advance(timeout)
            if idx == 1:
                mock_child.before = b"SSH-2.0-OpenSSH\r\n"
                return 12  # TIMEOUT
            if idx == 2:
                mock_child.before = b"SSH-2.0-OpenSSH\r\nkex banner...\r\n"
                return 12  # TIMEOUT
            if idx == 3:
                mock_child.before = b"SSH-2.0-OpenSSH\r\nkex banner...\r\nmotd..."
                return 12  # TIMEOUT
            mock_child.before = b"router1#"
            return 9  # prompt

        mock_child.expect.side_effect = expect_side_effect

        with (
            patch("connpy.core.pexpect.spawn", return_value=mock_child),
            patch("pexpect.fdpexpect.fdspawn", return_value=MagicMock()),
        ):
            res = n._connect(
                max_attempts=1,
                logger=lambda lvl, msg: logs.append((lvl, msg)),
                connect_timeout=10,
                clock=clock,
            )

        assert res is True
        assert call_count["n"] == 4
        warnings = [m for lvl, m in logs if lvl == "warning"]
        assert len(warnings) == 1
        assert "extending" in warnings[0].lower()
        mock_child.terminate.assert_not_called()

    def test_progress_extension_capped_at_max_ext_and_resets_handshake_per_attempt(self):
        clock = FakeClock(0.0)
        n = node("r1", "10.0.0.1", user="admin", protocol="ssh")
        children = []

        def spawn_side_effect(*args, **kwargs):
            c = MagicMock()
            c.logfile_read = None
            c.before = b""
            c.after = b""
            c.readline.return_value = b""
            step = {"k": 0}

            def exp_se(pats, timeout):
                step["k"] += 1
                clock.advance(timeout)
                # Keep growing bytes every timeout to prove cap at max_ext=3 (4 calls total per attempt)
                c.before = b"x" * (step["k"] * 10)
                return 12

            c.expect.side_effect = exp_se
            children.append(c)
            return c

        with patch("connpy.core.pexpect.spawn", side_effect=spawn_side_effect):
            res = n._connect(
                debug=True,
                max_attempts=2,
                connect_timeout=5,
                _default_max_total_seconds=100,
                clock=clock,
            )

        assert "Connection failed code: 12" in res
        assert "Connection timeout" in res
        # 2 attempts, each allowed 3 progress extensions -> 4 expect calls per child
        assert len(children) == 2
        for c in children:
            assert c.expect.call_count == 4
            assert c.logfile_read is None
            c.terminate.assert_called_with(force=True)
        assert n.mylog.closed is True
        # Idempotent close verification
        n.mylog.close()
        assert n.mylog.closed is True

    def test_global_deadline_bounds_multiple_attempts_and_clamps_expect_timeout(self):
        clock = FakeClock(0.0)
        n = node("r1", "10.0.0.1", user="admin", protocol="ssh")

        seen_timeouts = []
        children = []

        def spawn_side_effect(*args, **kwargs):
            c = MagicMock()
            c.logfile_read = None
            c.before = b""
            c.after = b""
            c.readline.return_value = b""

            def exp_se(pats, timeout):
                seen_timeouts.append(timeout)
                clock.advance(timeout)
                return 12  # TIMEOUT without progress -> triggers retry if remaining > 0

            c.expect.side_effect = exp_se
            children.append(c)
            return c

        with patch("connpy.core.pexpect.spawn", side_effect=spawn_side_effect):
            res = n._connect(
                max_attempts=3,
                connect_timeout=15,
                _default_max_total_seconds=35,
                clock=clock,
            )

        assert "Connection failed code: 12" in res
        assert "deadline" in res.lower()
        assert seen_timeouts == [15, 15, 5]
        assert clock() == 35.0
        assert len(children) == 3
        for c in children:
            c.terminate.assert_called_with(force=True)

    def test_pre_auth_buffer_cap_terminates_and_cleans_up(self):
        n = node("r1", "10.0.0.1", user="admin", protocol="ssh")
        mock_child = MagicMock()
        mock_child.logfile_read = None
        mock_child.expect.return_value = 9
        mock_child.before = b"A" * (MAX_PRE_AUTH_BUFFER_BYTES + 1)

        with patch("connpy.core.pexpect.spawn", return_value=mock_child):
            res = n._connect(debug=True)

        assert "Connection failed code: 12" in res
        assert "Pre-auth buffer exceeded" in res
        assert mock_child.logfile_read is None
        mock_child.terminate.assert_called_with(force=True)
        assert n.mylog.closed is True

    def test_secret_redaction_in_returned_errors_and_debug_logs(self):
        secret = "SuperSecretToken!42"
        n = node("r1", "10.0.0.1", user="admin", password=secret, protocol="ssh")
        logs = []

        mock_child = MagicMock()
        mock_child.logfile_read = None
        mock_child.expect.return_value = 2
        mock_child.before = f"Echoed: {secret}\r\n".encode()
        mock_child.after = b"Permission denied (publickey,password).\r\n"
        mock_child.readline.return_value = f"bad {secret}\r\n".encode()

        with patch("connpy.core.pexpect.spawn", return_value=mock_child):
            res = n._connect(debug=True, logger=lambda lvl, msg: logs.append((lvl, msg)))

        assert isinstance(res, str)
        assert secret not in res
        assert "***" in res
        assert all(secret not in msg for _, msg in logs)
        mock_child.terminate.assert_called_with(force=True)
        assert n.mylog.closed is True

    def test_parallel_8_nodes_isolated_cleanup_and_results(self):
        node_defs = {
            f"r{i}": {
                "host": f"10.0.0.{i}",
                "user": "admin",
                "password": f"secretPass{i}!",
                "protocol": "ssh",
            }
            for i in range(1, 9)
        }
        group = nodes(node_defs)

        def fake_spawn(prog, args, **kwargs):
            target_arg = args[-1]
            c = MagicMock()
            c.logfile_read = None
            c.child_fd = 100
            c.before = b""
            c.readline.return_value = b""
            if target_arg.endswith((".1", ".5")):
                c.expect.return_value = 1
                c.after = b"ssh: connect to host 10.0.0.1 port 22: Connection refused\r\n"
            else:
                c.expect.return_value = 9
                c.after = b"router#"
            return c

        with (
            patch("connpy.core.pexpect.spawn", side_effect=fake_spawn),
            patch("pexpect.fdpexpect.fdspawn", return_value=MagicMock()),
        ):
            out = group.run(["show version"], parallel=8)

        assert len(out) == 8
        assert group.status["r1"] == 1
        assert group.status["r5"] == 1
        for i in [2, 3, 4, 6, 7, 8]:
            assert group.status[f"r{i}"] == 0


# =============================================================================
# 5. compile_expects, bytes matching, adversarial MOTD, pexpect.EOF
# =============================================================================


class TestHardenedExpectsAndMotd:
    def test_compile_expects_compiles_ascii_to_bytes_with_dotall(self):
        raw_map = {
            "ssh": [
                r"(?m:^Connection refused(?:\r?\n))",
                pexpect.EOF,
                pexpect.TIMEOUT,
            ]
        }
        compiled = compile_expects(raw_map)
        assert isinstance(compiled["ssh"][0], re.Pattern)
        assert isinstance(compiled["ssh"][0].pattern, bytes)
        assert compiled["ssh"][0].flags & re.DOTALL
        assert compiled["ssh"][1] is pexpect.EOF
        assert compiled["ssh"][2] is pexpect.TIMEOUT

    def test_compile_expects_masks_re_unicode_when_recompiling_str_pattern(self):
        str_pat_default = re.compile(r"Connection refused")
        str_pat_explicit = re.compile(r"Permission denied.*", re.IGNORECASE | re.UNICODE)
        compiled = compile_expects({"ssh": [str_pat_default, str_pat_explicit]})

        for cpat in compiled["ssh"]:
            assert isinstance(cpat, re.Pattern)
            assert isinstance(cpat.pattern, bytes)
            assert cpat.flags & re.DOTALL
            assert not (cpat.flags & re.UNICODE)

        assert compiled["ssh"][1].flags & re.IGNORECASE
        assert compiled["ssh"][1].search(b"PERMISSION DENIED\nhost") is not None

    def test_adversarial_motd_banners_do_not_false_positive_and_connect(self):
        # 1) SSH adversarial MOTD: TACACS+ unavailable + Permission denied retry + chunked Connection refused + closed + not supported
        n_ssh = node("r_ssh", "10.0.0.1", user="admin", password="pw1234", protocol="ssh")
        banner_bytes = (
            b"Welcome to Corporate Router\r\n"
            b"TACACS+ server unavailable - using local fallback\r\n"
            b"Feature X not supported in legacy mode\r\n"
            b"Session timeout is 300s\r\n"
            b"Ticket closed by admin\r\n"
            b"Connection refused"
            b" by policy\r\n"
            b"Permission denied, please try again.\r\n"
            b"Password: "
        )
        # Verify none of the hardened ssh error regexes match the adversarial banner
        mock_child = MagicMock()
        mock_child.logfile_read = None
        mock_child.child_fd = 3

        # Expect checks using real pexpect pattern matching on a BytesIO-like feed
        captured_patterns = {}

        def _real_match_side_effect(patterns, timeout):
            # Check error patterns [1..7, 13..16] do NOT match banner_bytes
            for idx in [1, 2, 3, 4, 5, 6, 7, 13, 14, 15, 16]:
                pat = patterns[idx]
                assert pat.search(banner_bytes) is None, f"False positive on ssh index {idx}"
            # Verify password_prompt [8] DOES match the end of banner_bytes
            assert patterns[8].search(banner_bytes) is not None
            if not captured_patterns.get("calls"):
                captured_patterns["calls"] = [1]
                return 8  # password_prompt
            # After password sent, prompt arrives
            assert patterns[9].search(b"r_ssh#") is not None
            return 9

        mock_child.expect.side_effect = _real_match_side_effect
        with (
            patch("connpy.core.pexpect.spawn", return_value=mock_child),
            patch("pexpect.fdpexpect.fdspawn", return_value=MagicMock()),
        ):
            res = n_ssh._connect()
        assert res is True
        mock_child.sendline.assert_any_call("pw1234")

        # 2) SSM adversarial MOTD: "error: motd"
        n_ssm = node("r_ssm", "i-0123456789abcdef0", protocol="ssm")
        ssm_banner = b"MOTD:\r\nerror: motd notice only\r\nsh-5.1$ "
        ssm_child = MagicMock()
        ssm_child.child_fd = 4

        def _ssm_match_side_effect(patterns, timeout):
            for idx in [1, 2, 3, 4, 5, 6]:
                assert patterns[idx].search(ssm_banner) is None, f"False positive on ssm index {idx}"
            assert patterns[9].search(ssm_banner) is not None
            return 9

        ssm_child.expect.side_effect = _ssm_match_side_effect
        with (
            patch("connpy.core.pexpect.spawn", return_value=ssm_child),
            patch("pexpect.fdpexpect.fdspawn", return_value=MagicMock()),
        ):
            res_ssm = n_ssm._connect()
        assert res_ssm is True

        # 3) Telnet adversarial MOTD
        n_tel = node("r_tel", "10.0.0.1", user="admin", protocol="telnet")
        tel_banner = b"TACACS+ server unavailable\r\nConnection refused by policy\r\nrouter# "
        tel_child = MagicMock()
        tel_child.child_fd = 5

        def _tel_match_side_effect(patterns, timeout):
            for idx in [1, 2, 3, 4, 5, 6, 7, 13, 14, 15, 16]:
                assert patterns[idx].search(tel_banner) is None, f"False positive on telnet index {idx}"
            assert patterns[9].search(tel_banner) is not None
            return 9

        tel_child.expect.side_effect = _tel_match_side_effect
        with (
            patch("connpy.core.pexpect.spawn", return_value=tel_child),
            patch("pexpect.fdpexpect.fdspawn", return_value=MagicMock()),
        ):
            res_tel = n_tel._connect()
        assert res_tel is True

    def test_eof_evaluates_errors_without_trailing_newline(self):
        # SSH EOF without trailing newline preserves exact error code 1
        n_ssh = node("r1", "10.0.0.1", user="admin", protocol="ssh")
        mock_child = MagicMock()
        mock_child.expect.return_value = 11  # pexpect.EOF
        mock_child.before = b"ssh: connect to host 10.0.0.1 port 22: Connection refused"
        mock_child.after = pexpect.EOF
        mock_child.readline.return_value = b""

        with patch("connpy.core.pexpect.spawn", return_value=mock_child):
            res = n_ssh._connect()

        assert isinstance(res, str)
        assert "Connection failed code: 1" in res
        assert "Connection refused" in res
        mock_child.terminate.assert_called_with(force=True)

        # SSM EOF without trailing newline preserves exact error code 3
        n_ssm = node("s1", "i-0123456789abcdef0", protocol="ssm")
        ssm_child = MagicMock()
        ssm_child.expect.return_value = 10  # pexpect.EOF for ssm
        ssm_child.before = b"TargetNotConnected: i-0123456789abcdef0"
        ssm_child.after = pexpect.EOF
        ssm_child.readline.return_value = b""

        with patch("connpy.core.pexpect.spawn", return_value=ssm_child):
            res_ssm = n_ssm._connect()

        assert isinstance(res_ssm, str)
        assert "Connection failed code: 3" in res_ssm
        assert "TargetNotConnected" in res_ssm
        ssm_child.terminate.assert_called_with(force=True)

    def test_real_ssh_and_ssm_error_patterns_match_in_bytes(self):
        n_ssh = node("r1", "10.0.0.1", user="admin", protocol="ssh")
        mock_child = MagicMock()
        mock_child.expect.return_value = 2
        mock_child.before = b""
        mock_child.after = b"Permission denied (publickey,password).\r\n"
        mock_child.readline.return_value = b""

        def _cap(pats, timeout):
            assert pats[2].search(b"Permission denied (publickey,password).\r\n") is not None
            assert pats[1].search(b"ssh: connect to host 10.0.0.1 port 22: Connection refused\r\n") is not None
            assert pats[5].search(b"ssh: connect to host 10.0.0.1 port 22: Operation timed out\r\n") is not None
            assert pats[6].search(b"ssh: Could not resolve hostname bad: Name or service not known\r\n") is not None
            assert pats[7].search(b"Connection closed by remote host\r\n") is not None
            return 2

        mock_child.expect.side_effect = _cap
        with patch("connpy.core.pexpect.spawn", return_value=mock_child):
            res = n_ssh._connect()
        assert "Connection failed code: 2" in res

        # Ensure real error patterns also match for ssm [1,2,3,5,6]
        n_ssm = node("s1", "i-0123456789abcdef0", protocol="ssm")
        ssm_child = MagicMock()

        def _cap_ssm(pats, timeout):
            assert pats[1].search(b"Cannot perform start-session: timeout\r\n") is not None
            assert pats[2].search(b"An error occurred (AccessDeniedException) when calling StartSession\r\n") is not None
            assert pats[3].search(b"TargetNotConnected: i-0123456789abcdef0\r\n") is not None
            assert pats[5].search(b"UnknownClientException: error\r\n") is not None
            assert pats[6].search(b"Unable to locate credentials\r\n") is not None
            return 2

        ssm_child.expect.side_effect = _cap_ssm
        ssm_child.before = b""
        ssm_child.after = b"An error occurred (AccessDeniedException)\r\n"
        ssm_child.readline.return_value = b""
        with patch("connpy.core.pexpect.spawn", return_value=ssm_child):
            res_ssm = n_ssm._connect()
        assert "Connection failed code: 2" in res_ssm
