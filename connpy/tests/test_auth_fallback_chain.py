"""Comprehensive TDD suite for spec-05-auth-fallback-chain."""

from __future__ import annotations

import ast
import inspect
import json
import os
import pickle
import random
import string
import subprocess
import sys
import textwrap
import threading
import traceback
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from connpy import auth_utils
from connpy.auth_utils import (
    DEFAULT_TRACKER_TTL_SECONDS,
    MAX_AUTH_STREAM_BYTES,
    MAX_COMPOSE_EXPR_LEN,
    MAX_COMPOSE_VARIANTS,
    MAX_EOF_FALLBACK_ADVANCES,
    MAX_FALLBACK_ENTRIES,
    MAX_PROFILE_DEPTH,
    SAFE_ERROR_TEMPLATES,
    AuthAttemptTracker,
    SanitizedAuthError,
    extract_profile_refs_from_value,
    has_insecure_hostkey_checking,
    host_exact_re,
    mask_node_secrets,
    normalize_prompt_line,
    parse_compose_expression,
    raise_sanitized_auth_error,
    resolve_fallback_entries,
    sanitize_exception,
    sanitize_log_message,
    validate_fallback_entries,
    validate_json_depth_and_size,
)
from connpy.cli.config_handler import ConfigHandler
from connpy.cli.forms import Forms
from connpy.cli.validators import Validators
from connpy.configfile import configfile
from connpy.connapp import connapp
from connpy.core import node as NodeClass
from connpy.core import nodes as NodesClass
from connpy.grpc_layer import connpy_pb2
from connpy.grpc_layer.server import NodeServicer
from connpy.mcp_server import ConnpyMCPServer
from connpy.services.exceptions import InvalidConfigurationError
from connpy.services.execution_service import ExecutionService
from connpy.services.node_service import NodeService
from connpy.services.profile_service import ProfileService
from connpy.services.provider import ServiceProvider


class RaisingStdin:
    """Test stdin stub that fails immediately if unattended execution attempts any read."""

    def read(self, *args, **kwargs):
        raise AssertionError("stdin.read() MUST NOT be invoked during unattended execution")

    def readline(self, *args, **kwargs):
        raise AssertionError("stdin.readline() MUST NOT be invoked during unattended execution")

    def readlines(self, *args, **kwargs):
        raise AssertionError("stdin.readlines() MUST NOT be invoked during unattended execution")

    def __iter__(self):
        raise AssertionError("stdin.__iter__() MUST NOT be invoked during unattended execution")

    def isatty(self):
        return False


class FakeClock:
    """Deterministic monotonic clock for AuthAttemptTracker and timeout budget tests."""

    def __init__(self, initial: float = 100.0) -> None:
        self.now = float(initial)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += float(seconds)
        return self.now


def _make_mock_child(
    *,
    expect_fn,
    child_fd: int = 99,
) -> MagicMock:
    child = MagicMock()
    child.child_fd = child_fd
    child.before = b""
    child.after = b""
    child.buffer = b""
    child.logfile = None
    child.logfile_send = None
    child.logfile_read = None
    child.expect.side_effect = expect_fn
    child.readline.return_value = b""
    return child


def _make_config(tmp_path: Path, data: dict | None = None) -> configfile:
    conf_path = tmp_path / "config.yaml"
    key_path = tmp_path / ".osk"
    cfg = configfile(conf=str(conf_path), key=str(key_path))
    if data:
        def _init_mut(c):
            if "config" in data:
                c.config.update(data["config"])
            if "profiles" in data:
                c.profiles.update(data["profiles"])
            if "connections" in data:
                c.connections.update(data["connections"])

        cfg.transactional_update(_init_mut)
    return cfg


def _encrypt_with_config(cfg: configfile, plaintext: str) -> str:
    return cfg.encrypt(plaintext, keyfile=cfg.key)


# ---------------------------------------------------------------------------
# 1. Configfile contract, _extract, profile refs & Profile/NodeService
# ---------------------------------------------------------------------------


def test_configfile_getitem_extract_and_core_node_contract(tmp_path: Path) -> None:
    cfg = _make_config(
        tmp_path,
        {
            "profiles": {
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
                    "fallback": "",
                },
                "tacacs": {
                    "host": "",
                    "protocol": "ssh",
                    "port": "",
                    "user": "tac_admin",
                    "password": "tac_secret_password",
                    "options": "",
                    "logs": "",
                    "tags": "",
                    "jumphost": "",
                    "fallback": "",
                },
                "local_bak": {
                    "host": "",
                    "protocol": "ssh",
                    "port": "",
                    "user": "loc_admin",
                    "password": "loc_secret_password",
                    "options": "",
                    "logs": "",
                    "tags": "",
                    "jumphost": "",
                    "fallback": "",
                },
            }
        },
    )
    cfg._connections_add(
        id="r1",
        host="10.0.0.1",
        user="@tacacs",
        password="@tacacs",
        tags={"env": "lab"},
        fallback=["@local_bak", {"user": "emerg", "password": "compose:@local_bak+'-9'"}],
    )
    cfg._saveconfig(cfg.file)

    raw_item = cfg.getitem("r1", extract=False)
    assert raw_item["fallback"] == [
        "@local_bak",
        {"user": "emerg", "password": "compose:@local_bak+'-9'"},
    ]

    extracted = cfg.getitem("r1", extract=True)
    assert "fallback" not in extracted
    assert "_fallback" in extracted["tags"]
    assert extracted["tags"]["env"] == "lab"
    assert len(extracted["tags"]["_fallback"]) == 2
    assert extracted["tags"]["_fallback"][0]["user"] == "loc_admin"
    assert extracted["tags"]["_fallback"][1]["password"] == "compose:@local_bak+'-9'"

    # Original stored YAML/dict tags are untouched
    assert "_fallback" not in cfg.connections["r1"]["tags"]

    n = NodeClass("r1", **extracted, config=cfg)
    assert [c.user for c in n._auth_candidates] == ["tac_admin", "loc_admin", "emerg"]
    assert [c.is_fallback for c in n._auth_candidates] == [False, True, True]

    # _getallnodesfull(extract=True) does not mutate cfg.connections in-place
    full = cfg._getallnodesfull(extract=True)
    assert "r1" in full
    assert "_fallback" not in cfg.connections["r1"]["tags"]


def test_profileused_detects_compose_fallback_and_ignores_scalar_at(tmp_path: Path) -> None:
    cfg = _make_config(
        tmp_path,
        {
            "profiles": {
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
                    "fallback": "",
                },
                "tac": {
                    "host": "",
                    "protocol": "ssh",
                    "port": "",
                    "user": "u1",
                    "password": "p1",
                    "options": "",
                    "logs": "",
                    "tags": "",
                    "jumphost": "",
                    "fallback": "",
                },
                "pin": {
                    "host": "",
                    "protocol": "ssh",
                    "port": "",
                    "user": "u2",
                    "password": "99",
                    "options": "",
                    "logs": "",
                    "tags": "",
                    "jumphost": "",
                    "fallback": "",
                },
                "indirect": {
                    "host": "",
                    "protocol": "ssh",
                    "port": "",
                    "user": "u3",
                    "password": "@tac",
                    "options": "",
                    "logs": "",
                    "tags": "",
                    "jumphost": "",
                    "fallback": "",
                },
            }
        },
    )
    cfg._connections_add(id="n_compose", host="10.0.0.1", password="compose:@tac+@pin")
    cfg._connections_add(
        id="n_fb",
        host="10.0.0.2",
        fallback=[{"user": "b", "password": "@pin"}],
    )
    cfg._connections_add(id="n_indirect", host="10.0.0.3", password="@indirect")
    cfg._connections_add(id="n_literal", host="10.0.0.4", password="@foo+bar")
    cfg._saveconfig(cfg.file)

    assert sorted(cfg._profileused("pin")) == ["n_compose", "n_fb"]
    assert sorted(cfg._profileused("tac")) == ["n_compose", "n_indirect"]
    assert cfg._profileused("foo") == []
    assert extract_profile_refs_from_value("@foo+bar") == set()


def test_profile_service_and_node_service_preserve_scalar_and_fallback(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    p_svc = ProfileService(cfg)
    n_svc = NodeService(cfg)

    p_svc.add_profile("ops", {"user": "ops_u", "password": "ops_p", "fallback": ["@default"]})
    n_svc.add_node(
        "r_scalar",
        {"host": "10.0.0.9", "user": "u", "password": "@foo+bar", "fallback": ["@ops"]},
    )
    n_svc.update_node("r_scalar", {"fallback": [{"user": "bak", "password": "secret123"}]})

    details = n_svc.get_node_details("r_scalar")
    assert details["password"] == "@foo+bar"
    assert details["fallback"] == [{"user": "bak", "password": "secret123"}]

    resolved = p_svc.resolve_node_data(details)
    assert resolved["password"] == "@foo+bar"

    with pytest.raises(InvalidConfigurationError):
        n_svc.update_node(
            "r_scalar",
            {"fallback": [{"user": f"u{i}", "password": "p"} for i in range(6)]},
        )
    # Previous state rolled back cleanly
    assert n_svc.get_node_details("r_scalar")["fallback"] == [{"user": "bak", "password": "secret123"}]

    with pytest.raises(InvalidConfigurationError):
        p_svc.add_profile("bad_tag", {"tags": {"max_auth_prompts": 9}})

    p_svc.add_profile("cyc_a", {"password": "@cyc_b"})
    p_svc.add_profile("cyc_b", {"password": "@cyc_a"})
    with pytest.raises(InvalidConfigurationError):
        p_svc.get_profile("cyc_a", resolve=True)


# ---------------------------------------------------------------------------
# 2. Unattended Multi-Protocol State Machine, No Stdin, Logging & Anti-Spoof
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("protocol", ["ssh", "sftp", "telnet", "kubectl", "docker", "ssm", "mosh"])
def test_unattended_protocols_fallback_without_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    protocol: str,
) -> None:
    monkeypatch.setattr(sys, "stdin", RaisingStdin())
    monkeypatch.setattr("connpy.core.sleep", lambda _s: None)

    events: list[tuple[str, str]] = []
    children_created: list[MagicMock] = []
    connection_num = 0

    def _logger(level: str, msg: str) -> None:
        events.append(("log", f"{level}:{msg}"))

    def _spawn_side_effect(*_args, **_kwargs):
        nonlocal connection_num
        connection_num += 1
        conn_id = connection_num
        step = {"idx": 0}

        def _expect(pats, timeout=None):
            s = step["idx"]
            step["idx"] += 1
            if protocol in ("ssh", "sftp", "mosh"):
                if conn_id == 1:
                    if s == 0:
                        child.before = b""
                        child.after = b"Password:"
                        return 8
                    child.before = b""
                    child.after = b"Permission denied (publickey,password).\n"
                    return 2
                if s == 0:
                    child.before = b""
                    child.after = b"Password:"
                    return 8
                if s == 1:
                    child.before = b""
                    child.after = b"r1#"
                    return 9
                child.before = b""
                child.after = b"r1#"
                return 0
            else:
                # telnet / kubectl / docker / ssm: Username: -> Password: -> prompt or retry
                if s == 0:
                    child.before = b""
                    child.after = b"Username:"
                    return 0
                if s == 1:
                    child.before = b""
                    child.after = b"Password:"
                    return 5 if protocol == "kubectl" else 8
                if s == 2:
                    child.before = b""
                    child.after = b"Username:"
                    return 0
                if s == 3:
                    child.before = b""
                    child.after = b"Password:"
                    return 5 if protocol == "kubectl" else 8
                if s == 4:
                    child.before = b""
                    child.after = b"r1#"
                    return 6 if protocol == "kubectl" else 9
                child.before = b""
                child.after = b"r1#"
                return 0

        child = _make_mock_child(expect_fn=_expect)

        def _sendline(val=""):
            text = val.decode("utf-8", "replace") if isinstance(val, bytes) else str(val)
            events.append(("sendline", text))

        child.sendline.side_effect = _sendline
        children_created.append(child)
        return child

    cfg = _make_config(tmp_path)
    cfg._connections_add(
        id="dev1",
        host="i-0123456789abcdef0" if protocol == "ssm" else "router1",
        protocol=protocol,
        user="tac_user",
        password="TACACS_PRIMARY_SECRET",
        fallback=[{"user": "local_user", "password": "LOCAL_BACKUP_SECRET"}],
    )
    cfg._saveconfig(cfg.file)

    with (
        patch("connpy.core.shutil.which", return_value="/usr/bin/mosh"),
        patch("connpy.core.pexpect.spawn", side_effect=_spawn_side_effect),
        patch("pexpect.fdpexpect.fdspawn", return_value=MagicMock()),
    ):
        svc = ExecutionService(cfg)
        res = svc.run_commands("dev1", ["show version"], logger=_logger)

    assert res["dev1"]["status"] == 0
    assert res["dev1"]["fallback_used"] is True
    assert svc.last_fallback_used == {"dev1": True}

    # Pre-AUTH_OK hygiene: logfile* remained None on child during _connect
    for ch in children_created:
        assert ch.logfile is None
        assert ch.logfile_send is None

    # [AAA-FALLBACK] logged strictly BEFORE sendline of backup credentials, with zero secret leak
    log_indices = [i for i, (kind, payload) in enumerate(events) if kind == "log" and "[AAA-FALLBACK]" in payload]
    assert log_indices
    backup_send_indices = [
        i
        for i, (kind, payload) in enumerate(events)
        if kind == "sendline" and payload in ("local_user", "LOCAL_BACKUP_SECRET")
    ]
    assert backup_send_indices
    assert log_indices[0] < backup_send_indices[0]
    for kind, payload in events:
        if kind == "log":
            assert "TACACS_PRIMARY_SECRET" not in payload
            assert "LOCAL_BACKUP_SECRET" not in payload


def test_immutability_and_fail_open_stderr_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", RaisingStdin())
    n = NodeClass(
        "r_immut",
        host="10.0.0.5",
        user="prim_u",
        password="PRIMARY_PW_VALUE",
        fallback=[{"user": "bak_u", "password": "BACKUP_PW_VALUE"}],
    )
    orig_candidates = n._auth_candidates
    orig_hops = n._jumphost_hops

    conn_no = 0

    def _spawn(*_args, **_kwargs):
        nonlocal conn_no
        conn_no += 1
        cno = conn_no
        step = {"i": 0}

        def _expect(_pats, timeout=None):
            idx = step["i"]
            step["i"] += 1
            if cno == 1:
                if idx == 0:
                    ch.after = b"Password:"
                    return 8
                ch.after = b"Permission denied (publickey,password).\n"
                return 2
            if idx == 0:
                ch.after = b"Password:"
                return 8
            ch.after = b"r_immut#"
            return 9

        ch = _make_mock_child(expect_fn=_expect)
        return ch

    def _exploding_logger(_lvl: str, _msg: str) -> None:
        raise RuntimeError("logger sink exploded")

    with (
        patch("connpy.core.pexpect.spawn", side_effect=_spawn),
        patch("pexpect.fdpexpect.fdspawn", return_value=MagicMock()),
    ):
        ok = n._connect(logger=_exploding_logger, unattended=True)

    assert ok is True
    assert n.last_fallback_used is True
    captured = capsys.readouterr()
    assert "[AAA-FALLBACK]" in captured.err
    assert "BACKUP_PW_VALUE" not in captured.err
    assert "PRIMARY_PW_VALUE" not in captured.err

    # Strict immutability preserved
    assert n.user == "prim_u"
    assert n.password == ["PRIMARY_PW_VALUE"]
    assert n._auth_candidates == orig_candidates
    assert n._jumphost_hops == orig_hops


def test_allow_fallback_opt_out_and_no_creds_zero_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", RaisingStdin())
    n = NodeClass(
        "r_optout",
        host="10.0.0.8",
        user="u1",
        password="pw1",
        tags={"allow_fallback": False},
        fallback=[{"user": "bak", "password": "pw2"}],
    )
    sent: list[str] = []

    def _spawn(*_args, **_kwargs):
        step = {"i": 0}

        def _expect(_pats, timeout=None):
            idx = step["i"]
            step["i"] += 1
            if idx == 0:
                ch.after = b"Password:"
                return 8
            ch.after = b"Permission denied (publickey,password).\n"
            return 2

        ch = _make_mock_child(expect_fn=_expect)
        ch.sendline.side_effect = lambda v="": sent.append(str(v))
        return ch

    with patch("connpy.core.pexpect.spawn", side_effect=_spawn):
        res = n._connect(unattended=True)

    assert res is not True
    assert n.last_fallback_used is False
    assert sent == ["pw1"]

    # Missing creds returns [NO_CREDS] without reading stdin
    n_empty = NodeClass("r_nocreds", host="10.0.0.9", user="", password="")
    with patch("connpy.core.pexpect.spawn", side_effect=_spawn):
        res_empty = n_empty._connect(unattended=True)
    assert "[NO_CREDS]" in str(res_empty)


def test_host_exact_re_and_jumphost_anti_spoofing(tmp_path: Path) -> None:
    pat = host_exact_re("host1")
    assert pat.search("user@host1's password:")
    assert pat.search("[host1]: Password:")
    assert not pat.search("user@host10's password:")
    assert not pat.search("user@myhost1's password:")
    assert not pat.search("user@host1.attacker.com's password:")
    assert not pat.search("host1.attacker.com: Password:")

    # ANSI/CSI/OSC + CR overwrite stripping
    dirty = b"\x1b]0;spoof\x07attacker@evil\r\x1b[31madmin@host1's password:\x1b[0m "
    assert normalize_prompt_line(dirty, b"") == "admin@host1's password:"

    cfg = _make_config(tmp_path)
    cfg._connections_add(id="j1", host="jump1", user="ju1", password="jp1")
    cfg._connections_add(id="j2", host="jump2", user="ju2", password="jp2", jumphost="j1")
    cfg._connections_add(id="tgt", host="host1", user="tu", password="tp", jumphost="j2")
    cfg._saveconfig(cfg.file)

    # 1. Spoofing two hosts on the same line -> JUMP_AUTH_FAILED without sending
    n2 = NodeClass("tgt", **cfg.getitem("tgt", extract=True), config=cfg)
    sent_items: list[str] = []

    def _spawn_conflict(*_a, **_kw):
        ch = _make_mock_child(
            expect_fn=lambda _p, timeout=None: (
                setattr(ch, "before", b"jump1 "),
                setattr(ch, "after", b"host1 Password:"),
                8,
            )[-1]
        )
        ch.sendline.side_effect = lambda v="": sent_items.append(str(v))
        return ch

    with patch("connpy.core.pexpect.spawn", side_effect=_spawn_conflict):
        r_conflict = n2._connect(unattended=True)
    assert "[JUMP_AUTH_FAILED]" in str(r_conflict)
    assert sent_items == []

    # 2. Ambiguous generic prompt on single-hop fails closed unless opt-in
    cfg._connections_add(id="tgt1", host="target1", user="tu", password="tp", jumphost="j1")
    n_strict = NodeClass("tgt1", **cfg.getitem("tgt1", extract=True), config=cfg)

    def _spawn_generic(*_a, **_kw):
        step = {"i": 0}

        def _exp(_p, timeout=None):
            i = step["i"]
            step["i"] += 1
            if i == 0:
                ch.before = b""
                ch.after = b"Password:"
                return 8
            if i == 1:
                ch.before = b""
                ch.after = b"Password:"
                return 8
            ch.before = b""
            ch.after = b"tgt1#"
            return 9

        ch = _make_mock_child(expect_fn=_exp)
        ch.sendline.side_effect = lambda v="": sent_items.append(str(v))
        return ch

    with patch("connpy.core.pexpect.spawn", side_effect=_spawn_generic):
        r_ambig = n_strict._connect(unattended=True)
    assert "[JUMP_AUTH_FAILED]" in str(r_ambig)

    n_optin = NodeClass(
        "tgt1",
        **{**cfg.getitem("tgt1", extract=True), "tags": {"allow_ambiguous_single_hop_prompt": True}},
        config=cfg,
    )
    sent_items.clear()
    with (
        patch("connpy.core.pexpect.spawn", side_effect=_spawn_generic),
        patch("pexpect.fdpexpect.fdspawn", return_value=MagicMock()),
    ):
        r_ok = n_optin._connect(unattended=True)
    assert r_ok is True
    assert sent_items == ["jp1", "tp", ""]

    # 3. Backwards hop regression (jump2 prompted then jump1 again) -> JUMP_AUTH_FAILED
    def _spawn_regress(*_a, **_kw):
        step = {"i": 0}

        def _exp(_p, timeout=None):
            i = step["i"]
            step["i"] += 1
            if i == 0:
                ch.before = b"ju2@jump2's "
                ch.after = b"password:"
                return 8
            ch.before = b"ju1@jump1's "
            ch.after = b"password:"
            return 8

        ch = _make_mock_child(expect_fn=_exp)
        return ch

    with patch("connpy.core.pexpect.spawn", side_effect=_spawn_regress):
        r_reg = n2._connect(unattended=True)
    assert "[JUMP_AUTH_FAILED]" in str(r_reg)


def test_eof_budget_and_transport_error_no_fallback_and_stream_cap() -> None:
    # Transport error must NOT trigger fallback
    n_tr = NodeClass(
        "r_ref",
        host="10.0.0.10",
        user="u",
        password="p1",
        fallback=[{"user": "u2", "password": "p2"}],
    )
    calls = 0

    def _spawn_refused(*_a, **_kw):
        nonlocal calls
        calls += 1
        ch = _make_mock_child(
            expect_fn=lambda _p, timeout=None: (
                setattr(ch, "before", b""),
                setattr(ch, "after", b"ssh: connect to host 10.0.0.10 port 22: Connection refused\n"),
                1,
            )[-1]
        )
        return ch

    with patch("connpy.core.pexpect.spawn", side_effect=_spawn_refused):
        r_tr = n_tr._connect(unattended=True)
    assert "Connection refused" in str(r_tr)
    assert n_tr.last_fallback_used is False
    assert calls == 1

    # EOF advance cap: max 2 advances, 3rd EOF triggers EOF_BUDGET_EXHAUSTED
    n_eof = NodeClass(
        "r_eof",
        host="10.0.0.11",
        user="u",
        password="p0",
        fallback=[
            {"user": "u", "password": "p1"},
            {"user": "u", "password": "p2"},
            {"user": "u", "password": "p3"},
            {"user": "u", "password": "p4"},
        ],
    )

    def _spawn_eof(*_a, **_kw):
        step = {"i": 0}
        ch = None

        def _exp(_p, timeout=None):
            i = step["i"]
            step["i"] += 1
            if i == 0:
                ch.after = b"Password:"
                return 8
            ch.before = b""
            ch.after = b""
            return 11

        ch = _make_mock_child(expect_fn=_exp)
        return ch

    with patch("connpy.core.pexpect.spawn", side_effect=_spawn_eof):
        r_eof = n_eof._connect(unattended=True)
    assert "[EOF_BUDGET_EXHAUSTED]" in str(r_eof)

    # Pre-auth stream cap MAX_AUTH_STREAM_BYTES = 16384 in unattended mode
    def _spawn_big_buf(*_a, **_kw):
        ch = _make_mock_child(
            expect_fn=lambda _p, timeout=None: (
                setattr(ch, "before", b"A" * (MAX_AUTH_STREAM_BYTES + 1)),
                setattr(ch, "after", b"Password:"),
                8,
            )[-1]
        )
        return ch

    with patch("connpy.core.pexpect.spawn", side_effect=_spawn_big_buf):
        r_big = n_eof._connect(unattended=True)
    assert "16384" in str(r_big)


# ---------------------------------------------------------------------------
# 3. AuthAttemptTracker, LRU Anti-Flood Eviction & Concurrency
# ---------------------------------------------------------------------------


def test_tracker_anti_flood_eviction_ttl_and_concurrency() -> None:
    clock = FakeClock(1000.0)
    tracker = AuthAttemptTracker(
        ttl_seconds=DEFAULT_TRACKER_TTL_SECONDS,
        max_entries=10,
        default_max_host_attempts=2,
        clock=clock,
    )

    # Block target host A
    assert tracker.try_acquire_host("blocked.host", "22", "ssh", limit=2) is True
    assert tracker.try_acquire_host("blocked.host", "22", "ssh", limit=2) is True
    assert tracker.try_acquire_host("blocked.host", "22", "ssh", limit=2) is False

    # Flood with 50 distinct unblocked hosts (limit=5, only 1 attempt each)
    for i in range(50):
        assert tracker.try_acquire_host(f"flood-{i}", "22", "ssh", limit=5) is True

    assert tracker.entry_count() <= 10
    # Blocked entry MUST NOT have been evicted by unblocked flood
    assert tracker.remaining_host("blocked.host", "22", "ssh", limit=2) == 0
    assert tracker.try_acquire_host("blocked.host", "22", "ssh", limit=2) is False

    # Advance past TTL -> blocked entry expires cleanly
    clock.advance(DEFAULT_TRACKER_TTL_SECONDS + 1)
    assert tracker.remaining_host("blocked.host", "22", "ssh", limit=2) == 2
    assert tracker.try_acquire_host("blocked.host", "22", "ssh", limit=2) is True

    # Multi-host isolation + global user budget
    t2 = AuthAttemptTracker(default_max_global_user_attempts=2)
    assert t2.try_acquire_user("shared_admin") is True
    assert t2.try_acquire_user("shared_admin") is True
    assert t2.try_acquire_user("shared_admin") is False
    assert t2.try_acquire_host("host_a", "22", "ssh", limit=2) is True
    assert t2.try_acquire_host("host_b", "22", "ssh", limit=2) is True

    # Concurrent barrier test
    t_conc = AuthAttemptTracker(default_max_host_attempts=5)
    barrier = threading.Barrier(12)
    results: list[bool] = []
    lock = threading.Lock()

    def _worker():
        barrier.wait(timeout=5)
        ok = t_conc.try_acquire_host("conc.host", "22", "ssh", limit=5)
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=_worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert sum(1 for r in results if r) == 5
    assert len(results) == 12


def test_max_attempts_does_not_multiply_post_auth_and_budget_codes() -> None:
    tracker = AuthAttemptTracker(default_max_host_attempts=2)
    sent_pw: list[str] = []

    n = NodeClass(
        "r_budget",
        host="10.0.0.20",
        user="u",
        password="p1",
        fallback=[{"user": "u", "password": "p2"}, {"user": "u", "password": "p3"}],
    )

    def _spawn_reject(*_a, **_kw):
        step = {"i": 0}

        def _exp(_p, timeout=None):
            i = step["i"]
            step["i"] += 1
            if i == 0:
                ch.after = b"Password:"
                return 8
            ch.after = b"Permission denied (publickey,password).\n"
            return 2

        ch = _make_mock_child(expect_fn=_exp)
        ch.sendline.side_effect = lambda v="": sent_pw.append(str(v))
        return ch

    with patch("connpy.core.pexpect.spawn", side_effect=_spawn_reject):
        res = n._connect(max_attempts=3, unattended=True, attempt_tracker=tracker)

    assert "[HOST_BUDGET_EXHAUSTED]" in str(res)
    # Sent ONLY p1 and p2 (total=2); stopped cleanly before p3 without max_attempts=3 multiplying attempts
    assert sent_pw == ["p1", "p2"]

    # Global user budget code check
    t_user = AuthAttemptTracker()
    assert t_user.try_acquire_user("locked_u", limit=1) is True
    n_u = NodeClass(
        "r_ub",
        host="10.0.0.21",
        user="locked_u",
        password="p1",
        tags={"max_global_user_attempts": 1},
    )
    with patch("connpy.core.pexpect.spawn", side_effect=_spawn_reject):
        res_u = n_u._connect(unattended=True, attempt_tracker=t_user)
    assert "[USER_BUDGET_EXHAUSTED]" in str(res_u)


# ---------------------------------------------------------------------------
# 4. Golden Rule Sanitization, Pickle, Dynamic Traceback & AST Allowlist
# ---------------------------------------------------------------------------


def test_sanitized_auth_error_pickle_and_dynamic_traceback_clearing() -> None:
    err = SanitizedAuthError(SAFE_ERROR_TEMPLATES["AUTH_FAILED"], code="AUTH_FAILED")
    assert err.__suppress_context__ is True
    restored = pickle.loads(pickle.dumps(err))
    assert isinstance(restored, SanitizedAuthError)
    assert restored.code == "AUTH_FAILED"
    assert str(restored) == SAFE_ERROR_TEMPLATES["AUTH_FAILED"]

    secret_token = "TopSecretValue!#9911"

    def _boom():
        local_password = secret_token
        raise ValueError(f"boom with {local_password}")

    orig_exc: BaseException | None = None
    sanitized_err: SanitizedAuthError | None = None
    try:
        try:
            _boom()
        except ValueError as exc:
            orig_exc = exc
            raise_sanitized_auth_error(exc, code="AUTH_FAILED", secrets=[secret_token])
    except SanitizedAuthError as captured:
        sanitized_err = captured

    assert sanitized_err is not None
    assert sanitized_err.__cause__ is None
    assert sanitized_err.__context__ is None
    assert sanitized_err.__suppress_context__ is True
    formatted = "".join(
        traceback.format_exception(type(sanitized_err), sanitized_err, sanitized_err.__traceback__)
    )
    assert secret_token not in formatted
    assert "ValueError" not in formatted

    # Original exception's frames were cleared cleanly
    assert orig_exc is not None
    assert orig_exc.__traceback__ is None

    # Exiting except before raising also keeps __context__ None at VM level
    pending_err: SanitizedAuthError | None = None
    captured_outer: SanitizedAuthError | None = None
    try:
        try:
            _boom()
        except ValueError as exc:
            pending_err = sanitize_exception(exc, code="AUTH_FAILED", secrets=[secret_token])
        if pending_err is not None:
            pending_err.__context__ = None
            raise pending_err from None
    except SanitizedAuthError as outer:
        captured_outer = outer
    assert captured_outer is not None
    assert captured_outer.__context__ is None
    assert captured_outer.__cause__ is None
    assert captured_outer.__suppress_context__ is True


def test_ast_allowlist_and_no_raw_traceback_or_stdin_in_auth_surfaces() -> None:
    forbidden_tb_attrs = {"print_exc", "format_exc", "format_exception", "print_tb"}
    targets: list[tuple[str, object]] = [
        ("auth_utils", auth_utils),
        ("core._spawn_child", NodeClass._spawn_child),
        ("core._connect", NodeClass._connect),
        ("core.run", NodeClass.run),
        ("core.test", NodeClass.test),
        ("ExecutionService", ExecutionService),
        ("NodeServicer.interact_node", NodeServicer.interact_node),
        ("ConnpyMCPServer.run_commands", ConnpyMCPServer.run_commands),
        ("ConnpyMCPServer.test_commands", ConnpyMCPServer.test_commands),
    ]

    for label, obj in targets:
        target_obj = getattr(obj, "func", obj)
        if inspect.isfunction(target_obj) or inspect.ismethod(target_obj):
            target_obj = inspect.unwrap(target_obj)
        src = inspect.getsource(target_obj)
        tree = ast.parse(textwrap.dedent(src))
        for node_ast in ast.walk(tree):
            if isinstance(node_ast, ast.Attribute) and node_ast.attr in forbidden_tb_attrs:
                pytest.fail(f"Forbidden traceback call '.{node_ast.attr}' found in {label}")
            if (
                isinstance(node_ast, ast.Raise)
                and node_ast.cause is not None
                and not (isinstance(node_ast.cause, ast.Constant) and node_ast.cause.value is None)
            ):
                pytest.fail(f"Unsafe `raise ... from <cause>` found in {label}")
            if (
                label in ("core._connect", "core.run", "core.test")
                and isinstance(node_ast, ast.Attribute)
                and node_ast.attr == "stdin"
            ):
                pytest.fail(f"Prohibited `stdin` access found in {label}")


# ---------------------------------------------------------------------------
# 5. EBNF compose: Golden Cases + Fuzz, Lock Order & Atomic Transactions
# ---------------------------------------------------------------------------


def test_compose_ebnf_golden_cases_and_fuzz(tmp_path: Path) -> None:
    cfg = _make_config(
        tmp_path,
        {
            "profiles": {
                "p1": {"password": "Base"},
                "p2": {"password": ["A", "B"]},
                "pin": {"password": "99"},
                "self_ref": {"password": "compose:@self_ref+1"},
                "cyc_a": {"password": "compose:@cyc_b+1"},
                "cyc_b": {"password": "compose:@cyc_a+2"},
                "d5": {"password": "compose:@d4+5"},
                "d4": {"password": "compose:@d3+4"},
                "d3": {"password": "compose:@d2+3"},
                "d2": {"password": "compose:@d1+2"},
                "d1": {"password": "X"},
                "d6": {"password": "compose:@d5+6"},
                "empty_pw": {"password": ""},
            }
        },
    )
    rsa_blob = _encrypt_with_config(cfg, "RSA_SECRET")

    def _dec(blob: str) -> str:
        return NodeClass("x", host="1.1.1.1", config=cfg)._passtx([blob])[0]

    # Golden cases
    assert parse_compose_expression(f"compose:{rsa_blob}+'-suffix'", profiles=cfg.profiles, decrypt_fn=_dec) == [
        "RSA_SECRET-suffix"
    ]
    assert parse_compose_expression("compose:@@foo+'-'+'bar'", profiles=cfg.profiles) == ["@foo-bar"]
    assert parse_compose_expression("compose:'@foo+bar'", profiles=cfg.profiles) == ["@foo+bar"]
    assert parse_compose_expression("compose:@@", profiles=cfg.profiles) == ["@"]
    assert parse_compose_expression("compose:@p1+@p2+@pin", profiles=cfg.profiles) == [
        "BaseA99",
        "BaseB99",
    ]
    assert parse_compose_expression("compose:'x+y'+\" z\"", profiles=cfg.profiles) == ["x+y z"]
    assert parse_compose_expression("compose:@p1+@p1", profiles=cfg.profiles) == ["BaseBase"]
    assert parse_compose_expression("compose:@d5", profiles=cfg.profiles) == ["X2345"]

    # Too deep (depth 6 > 5)
    with pytest.raises(SanitizedAuthError):
        parse_compose_expression("compose:@d6", profiles=cfg.profiles)

    # Cycle
    with pytest.raises(SanitizedAuthError):
        parse_compose_expression("compose:@cyc_a", profiles=cfg.profiles)

    # Empty profile -> NO_CREDS
    with pytest.raises(SanitizedAuthError) as exc_info:
        parse_compose_expression("compose:@empty_pw+1", profiles=cfg.profiles)
    assert exc_info.value.code == "NO_CREDS"

    # Variants cap > 10
    cfg.profiles["many"] = {"password": [f"v{i}" for i in range(6)]}
    with pytest.raises(SanitizedAuthError):
        parse_compose_expression("compose:@many+@p2", profiles=cfg.profiles)

    # Max expr len > 4096
    with pytest.raises(SanitizedAuthError):
        parse_compose_expression("compose:" + ("a" * (MAX_COMPOSE_EXPR_LEN + 1)), profiles=cfg.profiles)

    # Property / fuzz test: random valid/malformed expressions never hang (linear O(n))
    rng = random.Random(42)
    alphabet = string.ascii_letters + "+@'\" \\"
    for _ in range(150):
        expr = "compose:" + "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 64)))
        try:
            res = parse_compose_expression(expr, profiles=cfg.profiles)
            assert isinstance(res, list)
            assert len(res) <= MAX_COMPOSE_VARIANTS
        except SanitizedAuthError:
            pass

    # Scalar password WITHOUT compose: NEVER triggers parser
    n_scalar = NodeClass("rs", host="10.0.0.1", user="u", password="@foo+bar", config=cfg)
    assert n_scalar.password == ["@foo+bar"]
    n_admin = NodeClass("ra", host="10.0.0.2", user="u", password="Admin+@2026", config=cfg)
    assert n_admin.password == ["Admin+@2026"]


def test_lock_ordering_no_deadlock_atomic_rollback_and_cache_dirty(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    p_svc = ProfileService(cfg)
    p_svc.add_profile("p1", {"user": "u1", "password": "pw1"})

    # 1. Verify @MethodHook on _saveconfig does NOT hold _mutation_lock (no deadlock under Barrier)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _hook(self_cfg, conf_path, result=None):
        barrier.wait(timeout=5)
        _ = p_svc.get_profile("p1", resolve=False)
        return result

    cfg._saveconfig.register_post_hook(_hook)

    def _concurrent_reader():
        try:
            barrier.wait(timeout=5)
            for _ in range(5):
                p_svc.list_profiles()
                cfg._getallnodes()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=_concurrent_reader)
    t.start()
    p_svc.add_profile("p2", {"user": "u2", "password": "pw2"})
    t.join(timeout=5)
    assert errors == []

    # Clear hook for subsequent checks
    if _hook in cfg._method_post_hooks.get("_saveconfig", []):
        cfg._method_post_hooks["_saveconfig"].remove(_hook)

    # 2. In-place rollback preserves id(cfg.connections) & YAML on KeyboardInterrupt / IOError
    orig_conn_id = id(cfg.connections)
    cfg._connections_add(id="stable", host="1.1.1.1")
    cfg._saveconfig(cfg.file)

    with pytest.raises(KeyboardInterrupt):
        def _abort(c):
            c.connections["temp"] = {"host": "2.2.2.2", "type": "connection"}
            raise KeyboardInterrupt()

        cfg.transactional_update(_abort)
    assert id(cfg.connections) == orig_conn_id
    assert "temp" not in cfg.connections

    with patch("connpy.configfile.yaml.dump", side_effect=OSError("disk full")), pytest.raises(OSError):
        cfg.transactional_update(
            lambda c: c._connections_add_unlocked(id="failed_node", host="9.9.9.9")
        )
    assert id(cfg.connections) == orig_conn_id
    assert "failed_node" not in cfg.connections

    # 3. Symlink refused + file permissions 0o600
    sym_target = tmp_path / "evil.yaml"
    sym_target.write_text("orig", encoding="utf-8")
    sym_conf = tmp_path / "sym_config.yaml"
    sym_conf.symlink_to(sym_target)
    with pytest.raises(OSError):
        cfg._atomic_write_text_unlocked(str(sym_conf), lambda fp: fp.write("pwned"))
    assert sym_target.read_text(encoding="utf-8") == "orig"
    assert (os.stat(cfg.file).st_mode & 0o777) == 0o600

    # 4. Post-replace cache write failure sets _cache_dirty=True WITHOUT reverting RAM; reads don't loop
    with patch.object(cfg, "_generate_nodes_cache_unlocked", side_effect=OSError("cache disk err")):
        cfg.transactional_update(lambda c: c._connections_add_unlocked(id="kept", host="3.3.3.3"))
    assert cfg._cache_dirty is True
    assert "kept" in cfg.connections
    assert cfg.getitem("kept")["host"] == "3.3.3.3"  # read succeeds & keeps _cache_dirty without infinite loop
    assert cfg._cache_dirty is True
    assert cfg.refresh_cache() is True
    assert cfg._cache_dirty is False

    # 5. Strict border validation in YAML _validate_config
    base_doc = {"config": {"case": False}, "connections": {}, "profiles": {}}
    assert cfg._validate_config(base_doc) is True
    for bad_prompts in (0, -1, True, False, "2", 6):
        bad = {"config": {"max_auth_prompts": bad_prompts}, "connections": {}, "profiles": {}}
        assert cfg._validate_config(bad) is False
    for bad_total in (0, -1, True, "5", 11):
        bad = {"config": {"max_total_auth_attempts": bad_total}, "connections": {}, "profiles": {}}
        assert cfg._validate_config(bad) is False
    assert cfg._validate_config({"config": {"disable_fallback_on_insecure_hostkey": "yes"}, "connections": {}, "profiles": {}}) is False


# ---------------------------------------------------------------------------
# 6. Fase C Surfaces: CLI, Validators, Forms, gRPC, MCP & Security Policy
# ---------------------------------------------------------------------------


def test_cli_validators_forms_and_config_handler_borders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = _make_config(
        tmp_path,
        {
            "profiles": {
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
                    "fallback": "",
                },
                "ops": {
                    "host": "",
                    "protocol": "ssh",
                    "port": "",
                    "user": "u",
                    "password": "p",
                    "options": "",
                    "logs": "",
                    "tags": "",
                    "jumphost": "",
                    "fallback": "",
                },
            }
        },
    )
    app = MagicMock()
    app.config = cfg
    app.profiles = list(cfg.profiles.keys())
    app.nodes_list = []
    app.services = ServiceProvider(cfg)

    val = Validators(app)
    assert val.fallback_validation({}, "@ops") is True
    assert val.fallback_validation({}, "compose:@ops+'1'") is True
    assert val.fallback_validation({}, '[{"user": "u", "password": "p"}]') is True
    assert val.tags_validation({}, "{'max_auth_prompts': 5, 'allow_fallback': False}") is True

    import inquirer

    with pytest.raises(inquirer.errors.ValidationError):
        val.fallback_validation({}, json.dumps([{"user": f"u{i}", "password": "p"} for i in range(6)]))
    with pytest.raises(inquirer.errors.ValidationError):
        val.fallback_validation({}, "x" * 9000)
    with pytest.raises(inquirer.errors.ValidationError):
        val.tags_validation({}, "{'max_auth_prompts': 0}")
    with pytest.raises(inquirer.errors.ValidationError):
        val.tags_validation({}, json.dumps({"a": {"b": {"c": {"d": {"e": 1}}}}}))

    # CLI argparse + ConfigHandler border checks + 0 reset
    app_real = connapp(cfg)
    parser, _ = app_real.get_parser()

    parsed = parser.parse_args(["config", "--max-auth-prompts", "4"])
    assert parsed.command == "max_auth_prompts"
    assert parsed.data == [4]

    handler = ConfigHandler(app_real)
    handler.dispatch(parsed)
    assert cfg.config["max_auth_prompts"] == 4

    # 0 unsets key
    parsed_zero = parser.parse_args(["config", "--max-auth-prompts", "0"])
    handler.dispatch(parsed_zero)
    assert "max_auth_prompts" not in cfg.config

    for bad_arg in (-1, 6, True, "abc"):
        ns = MagicMock(command="max_auth_prompts", data=[bad_arg])
        with pytest.raises(InvalidConfigurationError):
            handler.dispatch(ns)

    parsed_hk = parser.parse_args(["config", "--disable-fallback-on-insecure-hostkey", "true"])
    handler.dispatch(parsed_hk)
    assert cfg.config["disable_fallback_on_insecure_hostkey"] is True

    # Verify insecure hostkey blocks fallback only when flag is enabled
    n_hk = NodeClass(
        "r_hk",
        host="10.0.0.50",
        user="u",
        password="p1",
        options="-o StrictHostKeyChecking=no",
        fallback=[{"user": "u", "password": "p2"}],
        config=cfg,
    )
    assert has_insecure_hostkey_checking(n_hk.options, n_hk.tags) is True
    sent_hk: list[str] = []

    def _spawn_hk(*_a, **_kw):
        step = {"i": 0}

        def _exp(_p, timeout=None):
            i = step["i"]
            step["i"] += 1
            if i == 0:
                ch.after = b"Password:"
                return 8
            ch.after = b"Permission denied (publickey,password).\n"
            return 2

        ch = _make_mock_child(expect_fn=_exp)
        ch.sendline.side_effect = lambda v="": sent_hk.append(str(v))
        return ch

    with patch("connpy.core.pexpect.spawn", side_effect=_spawn_hk):
        res_hk = n_hk._connect(unattended=True)
    assert res_hk is not True
    assert sent_hk == ["p1"]

    # Forms edits include fallback cleanly
    forms = Forms(app)
    with patch("inquirer.prompt") as m_prompt:
        m_prompt.return_value = {
            "ids": "r_bulk",
            "location": "",
            "host": "10.1.1.1",
            "protocol": "ssh",
            "port": "22",
            "options": "",
            "logs": "",
            "tags": "{'max_auth_prompts': 2}",
            "jumphost": "",
            "user": "u1",
            "password": "No Password",
            "fallback": '[{"user": "u2", "password": "p2"}]',
        }
        bulk_ans = forms.questions_bulk()
    assert bulk_ans["fallback"] == [{"user": "u2", "password": "p2"}]


def test_mcp_masking_limits_sanitization_and_grpc_interact(tmp_path: Path) -> None:
    # Closed secret masking check + cycle + non-str keys
    cyc: dict[object, object] = {
        "password": "p",
        "fallback": [{"user": "u", "password": "secret"}, "@local"],
        "api_token": "tok",
        "custom_secret": "s",
        "tokens_per_min": 100,
        "max_tokens": 500,
        "token_count": 12,
        "ssh_key_path": "/home/u/.ssh/id_ed25519",
        "keyfile": "/path/.osk",
        42: "ok",
    }
    cyc["self"] = cyc
    masked = mask_node_secrets(cyc)
    assert masked["password"] == "***"
    assert masked["api_token"] == "***"
    assert masked["custom_secret"] == "***"
    assert masked["fallback"] == [{"user": "u", "password": "***"}, "***"]
    assert masked["tokens_per_min"] == 100
    assert masked["max_tokens"] == 500
    assert masked["token_count"] == 12
    assert masked["ssh_key_path"] == "/home/u/.ssh/id_ed25519"
    assert masked["keyfile"] == "/path/.osk"
    assert masked[42] == "ok"
    assert masked["self"] == "***"

    cfg = _make_config(tmp_path)
    svc_provider = ServiceProvider(cfg)
    mcp = ConnpyMCPServer(config=cfg, services=svc_provider, trust=True)

    assert mcp._mask_secrets({"password": "abc", "tokens_per_min": 5}) == {
        "password": "***",
        "tokens_per_min": 5,
    }

    # Add/update size, depth & fallback count enforcement
    too_deep = json.dumps({"a": {"b": {"c": {"d": {"e": 1}}}}})
    res_add = mcp.add_node("r1", {"host": "1.1.1.1", "tags": too_deep})
    assert res_add["success"] is False

    res_cmd_big = mcp.run_commands("r1", "a" * 9000)
    assert res_cmd_big["success"] is False

    # Sanitized error on unexpected exception during run_commands
    secret_pw = "UltraSecretMCP#77"
    with patch.object(
        svc_provider.execution,
        "run_commands",
        side_effect=RuntimeError(f"boom {secret_pw}"),
    ):
        err_run = mcp.run_commands("r1", ["show ver"])
    assert err_run["success"] is False
    assert secret_pw not in err_run["error"]
    assert "[AUTH_FAILED]" in err_run["error"]

    # fallback_used propagation in MCP test_commands
    with patch.object(svc_provider.execution, "test_commands", return_value={"r1": {"ok": True}}):
        svc_provider.execution.last_fallback_used = {"r1": True}
        test_res = mcp.test_commands("r1", ["show ver"], ["ok"])
    assert test_res["success"] is True
    assert test_res["fallback_used"] is True

    # gRPC NodeServicer.interact_node validation on connection_params_json
    class _AbortError(Exception):
        pass

    servicer = NodeServicer(provider=svc_provider)
    ctx = MagicMock()
    ctx.abort.side_effect = _AbortError("aborted")

    bad_req = connpy_pb2.InteractRequest(
        id="dyn",
        connection_params_json=json.dumps({"host": "1.1.1.1", "fallback": [{"user": f"u{i}", "password": "p"} for i in range(6)]}),
    )
    with pytest.raises(_AbortError, match="aborted"):
        list(servicer.interact_node(iter([bad_req]), ctx))
    ctx.abort.assert_called_with(
        pytest.importorskip("grpc").StatusCode.INVALID_ARGUMENT,
        "Fallback exceeds maximum of 5 entries",
    )


def test_compose_syntax_helpers_and_json_depth_limits() -> None:
    with pytest.raises(ValueError):
        validate_json_depth_and_size("[" * 6 + "1" + "]" * 6, max_depth=4)
    with pytest.raises(ValueError):
        validate_fallback_entries([{"user": "u", "password": "p"}] * (MAX_FALLBACK_ENTRIES + 1))
    assert resolve_fallback_entries(None, {}) == []
    assert sanitize_log_message("\x1b[31mhi\x07\npw=SecRet123", secrets=["SecRet123"]) == "hipw=***"
    assert sanitize_exception(ValueError("bad pw"), secrets=["bad"]).code == "AUTH_FAILED"
    assert MAX_PROFILE_DEPTH == 5
    assert MAX_COMPOSE_VARIANTS == 10
    assert MAX_EOF_FALLBACK_ADVANCES == 2


def test_nodes_group_fallback_tracking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", RaisingStdin())
    monkeypatch.setattr("connpy.core.sleep", lambda _s: None)
    cfg = _make_config(tmp_path)
    cfg._connections_add(
        id="n1",
        host="10.0.0.101",
        user="tac",
        password="p1",
        fallback=[{"user": "loc", "password": "p2"}],
    )
    cfg._saveconfig(cfg.file)

    conn_no = 0

    def _spawn(*_a, **_kw):
        nonlocal conn_no
        conn_no += 1
        cno = conn_no
        step = {"i": 0}

        def _exp(_p, timeout=None):
            i = step["i"]
            step["i"] += 1
            if cno == 1:
                if i == 0:
                    ch.after = b"Password:"
                    return 8
                ch.after = b"Permission denied (publickey,password).\n"
                return 2
            if i == 0:
                ch.after = b"Password:"
                return 8
            if i == 1:
                ch.after = b"n1#"
                return 9
            ch.after = b"n1#"
            return 0

        ch = _make_mock_child(expect_fn=_exp)
        return ch

    extracted = cfg.getitems(["n1"], extract=True)
    executor = NodesClass(extracted, config=cfg)
    with (
        patch("connpy.core.pexpect.spawn", side_effect=_spawn),
        patch("pexpect.fdpexpect.fdspawn", return_value=MagicMock()),
    ):
        executor.run(["show version"], parallel=1)
    assert executor.status["n1"] == 0
    assert executor.fallback_used == {"n1": True}


def test_signoff_fallback_inheritance_fail_closed_json_masking_and_atomic_budgets(
    tmp_path: Path,
) -> None:
    # 1a. @profile in fallback with own credentials + sub-fallback emits own first then sub-entries
    profiles_combo = {
        "tac_combo": {"user": "tac_u", "password": "tac_p", "fallback": ["@bak1", "@bak2"]},
        "bak1": {"user": "b1_u", "password": "b1_p"},
        "bak2": {"user": "b2_u", "password": "b2_p"},
        "empty_chain": {"user": "", "password": "", "fallback": ["@bak1"]},
        "cyc_a": {"user": "@cyc_b", "password": "p"},
        "cyc_b": {"user": "@cyc_a", "password": "p"},
    }
    res_combo = resolve_fallback_entries(["@tac_combo"], profiles_combo)
    assert res_combo == [
        {"user": "tac_u", "password": "tac_p"},
        {"user": "b1_u", "password": "b1_p"},
        {"user": "b2_u", "password": "b2_p"},
    ]
    assert resolve_fallback_entries(["@empty_chain"], profiles_combo) == [
        {"user": "b1_u", "password": "b1_p"}
    ]

    # Combined own + sub-fallback > MAX_FALLBACK_ENTRIES (5) fails closed
    profiles_cap = {
        "own_plus_4": {
            "user": "u0",
            "password": "p0",
            "fallback": [{"user": f"u{i}", "password": f"p{i}"} for i in range(1, 5)],
        },
        "own_plus_5": {
            "user": "u0",
            "password": "p0",
            "fallback": [{"user": f"u{i}", "password": f"p{i}"} for i in range(1, 6)],
        },
    }
    assert len(resolve_fallback_entries(["@own_plus_4"], profiles_cap)) == MAX_FALLBACK_ENTRIES
    with pytest.raises(ValueError, match="exceeds maximum"):
        resolve_fallback_entries(["@own_plus_5"], profiles_cap)

    # 1b. configfile.getitem(extract=True) inherits fallback from primary @profile when node omits it
    cfg = _make_config(
        tmp_path,
        {
            "profiles": {
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
                    "fallback": "",
                },
                "tacacs": {
                    "host": "",
                    "protocol": "ssh",
                    "port": "",
                    "user": "tac_u",
                    "password": "tac_secret_password",
                    "options": "",
                    "logs": "",
                    "tags": "",
                    "jumphost": "",
                    "fallback": ["@local_bak"],
                },
                "local_bak": {
                    "host": "",
                    "protocol": "ssh",
                    "port": "",
                    "user": "loc_u",
                    "password": "loc_secret_password",
                    "options": "",
                    "logs": "",
                    "tags": "",
                    "jumphost": "",
                    "fallback": "",
                },
            }
        },
    )
    cfg._connections_add(id="r_inherit", host="10.0.0.10", user="@tacacs", password="@tacacs")
    cfg._connections_add(
        id="r_pw_inherit",
        host="10.0.0.11",
        user="custom_u",
        password="@tacacs",
    )
    cfg._connections_add(
        id="r_explicit_fb",
        host="10.0.0.12",
        user="@tacacs",
        password="@tacacs",
        fallback=[{"user": "override_u", "password": "override_pw"}],
    )
    cfg._saveconfig(cfg.file)

    ext_inherit = cfg.getitem("r_inherit", extract=True)
    assert ext_inherit["user"] == "tac_u"
    assert ext_inherit["password"] == "tac_secret_password"
    assert ext_inherit["tags"]["_fallback"] == [
        {"user": "loc_u", "password": "loc_secret_password"}
    ]
    assert "tags" not in cfg.connections["r_inherit"] or "_fallback" not in str(
        cfg.connections["r_inherit"].get("tags", "")
    )
    n_inherit = NodeClass("r_inherit", **ext_inherit, config=cfg)
    assert [c.user for c in n_inherit._auth_candidates] == ["tac_u", "loc_u"]
    assert [c.is_fallback for c in n_inherit._auth_candidates] == [False, True]

    ext_pw = cfg.getitem("r_pw_inherit", extract=True)
    assert ext_pw["tags"]["_fallback"] == [{"user": "loc_u", "password": "loc_secret_password"}]
    ext_exp = cfg.getitem("r_explicit_fb", extract=True)
    assert ext_exp["tags"]["_fallback"] == [{"user": "override_u", "password": "override_pw"}]

    # 2. Fail-closed on missing refs, cycles, depth, rsa_blob without decrypt_fn, and empty list passwords
    for bad_fb in (
        ["@missing"],
        [{"user": "@missing", "password": "pw"}],
        [{"user": "u", "password": "@missing"}],
        [{"user": "u", "password": ["@missing"]}],
    ):
        with pytest.raises(ValueError):
            resolve_fallback_entries(bad_fb, profiles_combo)

    with pytest.raises(ValueError, match="Circular"):
        auth_utils._resolve_profile_field(profiles_combo, "cyc_a", "user")
    with pytest.raises(ValueError, match="Circular"):
        auth_utils._resolve_profile_field({"p": {"password": "x"}}, "p", "password", depth=MAX_PROFILE_DEPTH)

    rsa_blob = _encrypt_with_config(cfg, "RSA_SECRET")
    with pytest.raises(SanitizedAuthError) as exc_rsa:
        parse_compose_expression(f"compose:{rsa_blob}+'1'", profiles=cfg.profiles, decrypt_fn=None)
    assert exc_rsa.value.code == "NO_CREDS"

    with pytest.raises(SanitizedAuthError) as exc_rsa_prof:
        parse_compose_expression(
            "compose:@rsa_prof",
            profiles={"rsa_prof": {"password": rsa_blob}},
            decrypt_fn=None,
        )
    assert exc_rsa_prof.value.code == "NO_CREDS"

    with pytest.raises(ValueError, match="non-empty strings"):
        validate_fallback_entries([{"user": "u", "password": ["ok", ""]}])

    # Literal @foo+bar still allowed inside fallback entries
    assert resolve_fallback_entries([{"user": "u", "password": "@foo+bar"}], {}) == [
        {"user": "u", "password": "@foo+bar"}
    ]

    # 3. Control chars stripped BEFORE redacting (< 4 char + split secrets) & __context__ is None on compose decrypt error
    assert sanitize_log_message("pw=p\x071\x1b[31m ok", secrets=["p1"]) == "pw=*** ok"
    assert (
        sanitize_log_message("pw=Sec\x00\nRet99\x1b[32m!", secrets=["SecRet99"]) == "pw=***!"
    )

    def _bad_decrypt(_blob: str) -> str:
        secret = "InnerKeySecret#99"
        raise RuntimeError(f"decrypt error {secret}")

    with pytest.raises(SanitizedAuthError) as dec_err_info:
        parse_compose_expression(f"compose:{rsa_blob}", profiles={}, decrypt_fn=_bad_decrypt)
    assert dec_err_info.value.__context__ is None
    assert dec_err_info.value.__cause__ is None
    assert dec_err_info.value.__suppress_context__ is True
    assert "InnerKeySecret" not in str(dec_err_info.value)

    # Double-pass log redaction (both before and after control-char/ANSI stripping)
    assert sanitize_log_message("pw=\x07Sec!", secrets=["\x07Sec"]) == "pw=***!"

    # 4. RecursionError protection & fail-closed malformed JSON in top-level fallback
    deep_json = "[" * 1500 + "1" + "]" * 1500
    with pytest.raises(ValueError):
        validate_json_depth_and_size(deep_json)

    deep_ref_json = "[" * 1500 + '"@tacacs"' + "]" * 1500
    assert extract_profile_refs_from_value(deep_ref_json) == set()

    with pytest.raises(ValueError):
        validate_fallback_entries('[{"user":"a","password":"s3cr3t"')
    with pytest.raises(ValueError):
        validate_fallback_entries("{Admin}123")
    assert validate_fallback_entries([{"user": "u", "password": "{Admin}123"}]) == [
        {"user": "u", "password": "{Admin}123"}
    ]
    with pytest.raises(ValueError):
        validate_fallback_entries("[[[[[1]]]]]")
    with pytest.raises(ValueError):
        validate_fallback_entries("[" + "a" * 9000)

    # 5a. mask_node_secrets masks tags._fallback from fresh getitem(extract=True)
    masked_extracted = mask_node_secrets(cfg.getitem("r_inherit", extract=True))
    assert masked_extracted["password"] == "***"
    assert masked_extracted["tags"]["_fallback"] == [{"user": "loc_u", "password": "***"}]

    # 5b. AuthAttemptTracker parameter validation & atomic multi-budget acquisition
    for bad_ttl in (0, -5, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            AuthAttemptTracker(ttl_seconds=bad_ttl)
    for bad_bool_ttl in (True, False, "10"):
        with pytest.raises(TypeError):
            AuthAttemptTracker(ttl_seconds=bad_bool_ttl)
    for bad_entries in (0, -1):
        with pytest.raises(ValueError):
            AuthAttemptTracker(max_entries=bad_entries)
    for bad_bool_entries in (True, False, "5"):
        with pytest.raises(TypeError):
            AuthAttemptTracker(max_entries=bad_bool_entries)

    # Exhaust host budget first -> trying (user='shared_u', host='locked.host') must NOT consume user budget
    t_atomic = AuthAttemptTracker(default_max_host_attempts=1, default_max_global_user_attempts=1)
    assert t_atomic.try_acquire_host("locked.host", "22", "ssh", limit=1) is True
    assert t_atomic.remaining_user("shared_u", limit=1) == 1

    ok, code = t_atomic.try_acquire_target_attempt(
        host="locked.host",
        port="22",
        protocol="ssh",
        user="shared_u",
        host_limit=1,
        user_limit=1,
    )
    assert ok is False
    assert code == "HOST_BUDGET_EXHAUSTED"
    assert t_atomic.remaining_user("shared_u", limit=1) == 1

    # Full _connect flow: host exhausted must return HOST_BUDGET_EXHAUSTED and leave shared_u intact
    n_locked = NodeClass(
        "r_locked",
        host="locked.host",
        port="22",
        user="shared_u",
        password="secret_pw",
        tags={"max_total_auth_attempts": 1, "max_global_user_attempts": 1},
    )

    def _spawn_prompt(*_a, **_kw):
        ch = _make_mock_child(
            expect_fn=lambda _p, timeout=None: (
                setattr(ch, "after", b"Password:"),
                8,
            )[-1]
        )
        return ch

    with patch("connpy.core.pexpect.spawn", side_effect=_spawn_prompt):
        res_locked = n_locked._connect(unattended=True, attempt_tracker=t_atomic)
    assert "[HOST_BUDGET_EXHAUSTED]" in str(res_locked)
    assert t_atomic.remaining_user("shared_u", limit=1) == 1


def test_signoff_ronda2_eof_empty_profiles_host_re_shlex_and_lazy_import() -> None:
    # (a) Single-candidate EOF after sending password -> AUTH_FAILED; premature EOF -> TRANSPORT_ERROR
    n_single = NodeClass(
        "r_eof_single",
        host="10.0.0.80",
        user="u",
        password="p1",
    )

    def _spawn_eof_after_pw(*_a, **_kw):
        step = {"i": 0}
        ch = None

        def _exp(_p, timeout=None):
            i = step["i"]
            step["i"] += 1
            if i == 0:
                ch.after = b"Password:"
                return 8
            ch.before = b""
            ch.after = b""
            return 11

        ch = _make_mock_child(expect_fn=_exp)
        return ch

    with patch("connpy.core.pexpect.spawn", side_effect=_spawn_eof_after_pw):
        res_after = n_single._connect(unattended=True)
    assert res_after is not True
    assert "[AUTH_FAILED]" in str(res_after)

    def _spawn_eof_before_pw(*_a, **_kw):
        ch = _make_mock_child(
            expect_fn=lambda _p, timeout=None: (
                setattr(ch, "before", b""),
                setattr(ch, "after", b""),
                11,
            )[-1]
        )
        return ch

    with patch("connpy.core.pexpect.spawn", side_effect=_spawn_eof_before_pw):
        res_before = n_single._connect(unattended=True)
    assert res_before is not True
    assert "[TRANSPORT_ERROR]" in str(res_before)

    # (b) Empty referenced fallback profiles must fail closed
    with pytest.raises(ValueError, match="no credentials"):
        resolve_fallback_entries(["@empty_prof"], {"empty_prof": {"user": "", "password": ""}})
    with pytest.raises(ValueError, match="no credentials"):
        resolve_fallback_entries(
            ["@empty_sub"],
            {"empty_sub": {"user": "", "password": "", "fallback": "[]"}},
        )

    # (c) host_exact_re boundary coverage
    host_re = host_exact_re("host1")
    for matching_prompt in (
        "host1#",
        "user@host1>",
        "user@host1$",
        "(user@host1) Password:",
        "host1, ",
    ):
        assert host_re.search(matching_prompt) is not None
    for non_matching_prompt in ("host10#", "host1.attacker.com#"):
        assert host_re.search(non_matching_prompt) is None

    # (d) shlex-based insecure hostkey detection
    for insecure_opt in (
        '-o "StrictHostKeyChecking=no"',
        "-o StrictHostKeyChecking no",
        "-o StrictHostKeyChecking=off",
        "-oStrictHostKeyChecking=no",
        "-o UserKnownHostsFile=/dev/null",
        "-oUserKnownHostsFile=/dev/null",
    ):
        assert has_insecure_hostkey_checking(insecure_opt) is True
    assert has_insecure_hostkey_checking("-o StrictHostKeyChecking=yes") is False

    # (e) Isolated import of connpy.auth_utils + AuthAttemptTracker strict limit validation
    repo_root = str(Path(__file__).resolve().parents[2])
    subprocess.run(
        [sys.executable, "-c", "import connpy.auth_utils"],
        check=True,
        cwd=repo_root,
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, connpy.auth_utils as au; "
                "assert 'connpy.services' not in sys.modules; "
                "from connpy.services.exceptions import ConnpyError; "
                "assert issubclass(au.SanitizedAuthError, ConnpyError)"
            ),
        ],
        check=True,
        cwd=repo_root,
    )
    for bad_limit in (True, False, 0, -1, 11, "2"):
        with pytest.raises(ValueError):
            AuthAttemptTracker(default_max_host_attempts=bad_limit)
        with pytest.raises(ValueError):
            AuthAttemptTracker(default_max_hop_attempts=bad_limit)
    for bad_user_limit in (True, False, 0, -1, 1001, "2"):
        with pytest.raises(ValueError):
            AuthAttemptTracker(default_max_global_user_attempts=bad_user_limit)


def test_forms_prompt_sequence_and_safe_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = _make_config(
        tmp_path,
        {
            "profiles": {
                "default": {"protocol": "ssh", "fallback": ""},
                "p1": {"user": "u1", "password": "pw1"},
                "p2": {"user": "u2", "password": "pw2"},
                "fb": {"user": "fb_u", "password": "fb_p"},
            }
        },
    )
    app = MagicMock()
    app.config = cfg
    app.profiles = list(cfg.profiles.keys())
    app.nodes_list = []
    app.services = ServiceProvider(cfg)
    forms = Forms(app)

    # 1. questions_nodes: base questions -> passq -> fallback; spaces stripped in Profiles list
    recorded_nodes: list[list[tuple[str, str]]] = []
    node_answers = iter([
        {
            "host": "10.0.0.1",
            "protocol": "ssh",
            "port": "22",
            "options": "",
            "logs": "",
            "tags": "",
            "jumphost": "",
            "user": "u",
            "password": "Profiles",
        },
        {"password": " @p1 , @p2 "},
        {"fallback": "@fb"},
    ])

    def _mock_prompt_nodes(qs, **_kw):
        recorded_nodes.append([(q.name, str(getattr(q, "message", ""))) for q in qs])
        return next(node_answers)

    with patch("inquirer.prompt", side_effect=_mock_prompt_nodes):
        node_res = forms.questions_nodes("r_ux", uniques={"type": "connection"})

    assert isinstance(node_res, dict)
    assert node_res["password"] == ["@p1", "@p2"]
    assert node_res["fallback"] == "@fb"
    assert len(recorded_nodes) == 3
    first_names = [n for n, _ in recorded_nodes[0]]
    assert "password" in first_names
    assert "fallback" not in first_names
    assert [n for n, _ in recorded_nodes[1]] == ["password"]
    assert "@profile" in recorded_nodes[1][0][1]
    assert [n for n, _ in recorded_nodes[2]] == ["fallback"]

    # Cancel during passq returns False safely without raising
    with patch(
        "inquirer.prompt",
        side_effect=[{"host": "10.0.0.1", "password": "Local Password"}, None],
    ):
        assert forms.questions_nodes("r_cancel", uniques={"type": "connection"}) is False

    # Cancel during fbq returns False safely
    with patch(
        "inquirer.prompt",
        side_effect=[{"host": "10.0.0.1", "password": "No Password"}, None],
    ):
        assert forms.questions_nodes("r_cancel_fb", uniques={"type": "connection"}) is False

    # 2. questions_bulk: base questions -> passq -> fallback, plus cancel on passq & fbq
    recorded_bulk: list[list[tuple[str, str]]] = []
    bulk_answers = iter([
        {
            "ids": "r_b1",
            "location": "",
            "host": "10.1.1.1",
            "protocol": "ssh",
            "port": "22",
            "options": "",
            "logs": "",
            "tags": "",
            "jumphost": "",
            "user": "u1",
            "password": "Local Password",
        },
        {"password": "MyBulkPassword"},
        {"fallback": '[{"user": "bak", "password": "p2"}]'},
    ])

    def _mock_prompt_bulk(qs, **_kw):
        recorded_bulk.append([(q.name, str(getattr(q, "message", ""))) for q in qs])
        return next(bulk_answers)

    with patch("inquirer.prompt", side_effect=_mock_prompt_bulk):
        bulk_res = forms.questions_bulk()

    assert isinstance(bulk_res, dict)
    assert bulk_res["fallback"] == [{"user": "bak", "password": "p2"}]
    assert len(recorded_bulk) == 3
    bulk_first = [n for n, _ in recorded_bulk[0]]
    assert "password" in bulk_first
    assert "fallback" not in bulk_first
    assert recorded_bulk[1] == [("password", "Set Password")]
    assert [n for n, _ in recorded_bulk[2]] == ["fallback"]

    with patch(
        "inquirer.prompt",
        side_effect=[{"ids": "r_b2", "password": "Local Password"}, None],
    ):
        assert forms.questions_bulk() is False

    with patch(
        "inquirer.prompt",
        side_effect=[{"ids": "r_b3", "password": "No Password"}, None],
    ):
        assert forms.questions_bulk() is False


def test_multi_profile_fallback_inheritance_priority_and_password_list(tmp_path: Path) -> None:
    cfg = _make_config(
        tmp_path,
        {
            "profiles": {
                "default": {"protocol": "ssh", "fallback": ""},
                "aws": {"options": "-i ~/.ssh/id_rsa", "fallback": ""},
                "dish": {"user": "federico.luzzi", "password": "dish_password", "fallback": "@sdnc"},
                "sdnc": {"user": "sdnc_admin", "password": "sdnc_password", "fallback": ""},
            }
        },
    )
    cfg._connections_add(
        id="n_multi",
        host="10.230.86.213",
        options="@aws",
        jumphost="",
        user="@dish",
        password=["@dish"],
        fallback="",
    )
    cfg._saveconfig(cfg.file)

    p_svc = ProfileService(cfg)
    raw_details = cfg.getitem("n_multi", extract=False)
    resolved_svc = p_svc.resolve_node_data(raw_details)
    assert resolved_svc["user"] == "federico.luzzi"
    assert resolved_svc["password"] == ["dish_password"]
    assert resolved_svc["options"] == "-i ~/.ssh/id_rsa"
    assert resolved_svc["fallback"] == "@sdnc"

    resolved_jh_prio = p_svc.resolve_node_data({
        "host": "10.230.86.214",
        "jumphost": "@aws",
        "user": "@dish",
        "password": ["@dish"],
        "fallback": "",
    })
    assert resolved_jh_prio["fallback"] == "@sdnc"

    extracted = cfg.getitem("n_multi", extract=True)
    assert extracted["tags"]["_fallback"] == [{"user": "sdnc_admin", "password": "sdnc_password"}]
    n_multi = NodeClass("n_multi", **extracted, config=cfg)
    assert [(c.user, c.password_spec, c.is_fallback) for c in n_multi._auth_candidates] == [
        ("federico.luzzi", "dish_password", False),
        ("sdnc_admin", "sdnc_password", True),
    ]


def test_interactive_mode_fallback_proxycommand_sshkey_and_no_password_ux(tmp_path: Path) -> None:
    cfg = _make_config(
        tmp_path,
        {
            "profiles": {
                "default": {"protocol": "ssh", "fallback": ""},
                "aws": {"user": "ec2-user", "password": "", "options": "-i ~/.ssh/id_rsa", "fallback": ""},
                "dish": {"user": "federico.luzzi", "password": "dish_password", "fallback": "@sdnc"},
                "sdnc": {"user": "sdnc_admin", "password": "sdnc_password", "fallback": ""},
            },
            "connections": {
                "aws_bastion": {
                    "host": "bastion.example.internal",
                    "protocol": "ssh",
                    "port": "",
                    "user": "ec2-user",
                    "password": "",
                    "options": "-i ~/.ssh/id_rsa",
                    "logs": "",
                    "tags": "",
                    "jumphost": "",
                    "type": "connection",
                },
                "field_router": {
                    "host": "10.230.86.213",
                    "protocol": "ssh",
                    "port": "",
                    "user": "@dish",
                    "password": ["@dish"],
                    "options": "",
                    "logs": "",
                    "tags": "",
                    "jumphost": "aws_bastion",
                    "fallback": "",
                    "type": "connection",
                },
            },
        },
    )
    extracted = cfg.getitem("field_router", extract=True)
    n = NodeClass("field_router", **extracted, config=cfg)
    assert len(n._jumphost_hops) == 1
    assert n._jumphost_hops[0]["passwords"] == ()

    # Case 1: Interactive mode (unattended=False) hits primary reject -> generic password prompt ->
    # respawns as sdnc_admin, handles generic password prompt via SSH-key hop disambiguation, succeeds!
    spawn_cmds: list[str] = []
    sent_passwords: list[str] = []
    events: list[str] = []

    def _logger(level: str, msg: str) -> None:
        events.append(f"{level}:{msg}")

    def _spawn_field(*args, **_kw):
        cmd_tokens: list[str] = []
        if args:
            if isinstance(args[0], (list, tuple)):
                cmd_tokens.extend(str(x) for x in args[0])
            else:
                cmd_tokens.append(str(args[0]))
            if len(args) > 1 and isinstance(args[1], (list, tuple)):
                cmd_tokens.extend(str(x) for x in args[1])
        spawn_cmds.append(" ".join(cmd_tokens))
        conn_no = len(spawn_cmds)
        step = {"i": 0}
        ch = None

        def _exp(_p, timeout=None):
            i = step["i"]
            step["i"] += 1
            if conn_no == 1:
                if i == 0:
                    ch.before = b"federico.luzzi@10.230.86.213's "
                    ch.after = b"password:"
                    return 8
                # Router without TACACS sends generic password: prompt on retry
                ch.before = b""
                ch.after = b"password:"
                return 8
            if i == 0:
                # Spawn #2 receives generic password: prompt with SSH-key ProxyCommand hop
                ch.before = b""
                ch.after = b"password:"
                return 8
            ch.before = b""
            ch.after = b"router#"
            return 9

        ch = _make_mock_child(expect_fn=_exp)
        ch.sendline.side_effect = lambda v="": (
            events.append(f"send:{v}"),
            sent_passwords.append(str(v)),
        )[-1]
        return ch

    with (
        patch("connpy.core.pexpect.spawn", side_effect=_spawn_field),
        patch("pexpect.fdpexpect.fdspawn", return_value=MagicMock()),
    ):
        res_ok = n._connect(unattended=False, logger=_logger)

    assert res_ok is True
    assert n.last_fallback_used is True
    assert getattr(n, "missingtext", False) is False
    assert len(spawn_cmds) == 2
    assert "federico.luzzi@10.230.86.213" in spawn_cmds[0]
    assert "sdnc_admin@10.230.86.213" in spawn_cmds[1]
    assert sent_passwords == ["dish_password", "sdnc_password", ""]
    aaa_idx = next(i for i, e in enumerate(events) if "[AAA-FALLBACK]" in e)
    send_fb_idx = next(i for i, e in enumerate(events) if e == "send:sdnc_password")
    assert aaa_idx < send_fb_idx

    # Case 2: Configured credentials all rejected in unattended=False -> [AUTH_FAILED], no missingtext, no Connected
    n_fail = NodeClass("field_router", **cfg.getitem("field_router", extract=True), config=cfg)
    fail_logs: list[str] = []

    def _spawn_all_reject(*_args, **_kw):
        ch = _make_mock_child(
            expect_fn=lambda _p, timeout=None: (
                setattr(ch, "before", b""),
                setattr(ch, "after", b"password:"),
                8,
            )[-1]
        )
        return ch

    with patch("connpy.core.pexpect.spawn", side_effect=_spawn_all_reject):
        res_fail = n_fail._connect(unattended=False)
    assert res_fail is not True
    assert "[AUTH_FAILED]" in str(res_fail)
    assert getattr(n_fail, "missingtext", False) is False

    with (
        patch("connpy.core.pexpect.spawn", side_effect=_spawn_all_reject),
        pytest.raises(SystemExit) as sys_exit_info,
    ):
        n_fail.interact(logger=lambda lvl, msg: fail_logs.append(f"{lvl}:{msg}"))
    assert sys_exit_info.value.code == 1
    assert not any("Connected to" in entry for entry in fail_logs)

    # Case 3: "No Password" node sets missingtext=True in unattended=False and avoids duplicate print in async_mode=True
    n_nopw = NodeClass("r_nopw", host="10.0.0.99", user="op", password="", fallback="")

    def _spawn_nopw(*_args, **_kw):
        ch = _make_mock_child(
            expect_fn=lambda _p, timeout=None: (
                setattr(ch, "before", b""),
                setattr(ch, "after", b"op@10.0.0.99's password:"),
                8,
            )[-1]
        )
        return ch

    with (
        patch("connpy.core.pexpect.spawn", side_effect=_spawn_nopw),
        patch("pexpect.fdpexpect.fdspawn", return_value=MagicMock()),
    ):
        res_nopw = n_nopw._connect(unattended=False)
    assert res_nopw is True
    assert getattr(n_nopw, "missingtext", False) is True
    n_nopw.idletime = 0

    with patch("builtins.print") as m_print:
        n_nopw._setup_interact_environment(async_mode=True)
    m_print.assert_not_called()

    with patch("builtins.print") as m_print_sync:
        n_nopw._setup_interact_environment(async_mode=False)
    m_print_sync.assert_called_once()


def test_profile_service_strict_auth_inheritance_no_options_leak_and_no_non_auth_fallback(
    tmp_path: Path,
) -> None:
    cfg = _make_config(
        tmp_path,
        {
            "profiles": {
                "default": {
                    "protocol": "ssh",
                    "logs": "/tmp/conn.log",
                    "options": "",
                    "fallback": "",
                },
                "6wind": {"tags": {"os": "6wind"}, "options": "", "fallback": ""},
                "dish": {
                    "user": "federico.luzzi",
                    "password": "dish_password",
                    "options": "-J jumpconnect",
                    "fallback": "@sdnc",
                },
                "sdnc": {
                    "user": "sdnc_admin",
                    "password": "sdnc_password",
                    "options": "",
                    "fallback": "",
                },
                "aws_with_fb": {
                    "options": "-i ~/.ssh/id_rsa",
                    "fallback": "@sdnc",
                },
            },
            "connections": {
                "dish": {
                    "type": "folder",
                    "jumpintconnect": {
                        "type": "connection",
                        "host": "10.10.0.1",
                        "protocol": "ssh",
                        "port": "",
                        "user": "jump_u1",
                        "password": "",
                        "options": "",
                        "logs": "",
                        "tags": "",
                        "jumphost": "",
                    },
                    "jumpintchy": {
                        "type": "connection",
                        "host": "10.10.0.2",
                        "protocol": "ssh",
                        "port": "",
                        "user": "jump_u2",
                        "password": "",
                        "options": "",
                        "logs": "",
                        "tags": "",
                        "jumphost": "jumpintconnect@dish",
                    },
                    "xw_int": {
                        "type": "connection",
                        "host": "172.31.10.12",
                        "protocol": "ssh",
                        "port": "",
                        "options": "",
                        "logs": "@default",
                        "tags": "@6wind",
                        "user": "@dish",
                        "password": ["@dish"],
                        "jumphost": "jumpintchy@dish",
                        "fallback": "",
                    },
                },
            },
        },
    )

    p_svc = ProfileService(cfg)
    raw_xw = cfg.getitem("xw_int@dish", extract=False)
    resolved_xw = p_svc.resolve_node_data(raw_xw)

    assert resolved_xw["options"] == ""
    assert "-J jumpconnect" not in resolved_xw["options"]
    assert resolved_xw["user"] == "federico.luzzi"
    assert resolved_xw["password"] == ["dish_password"]
    assert resolved_xw["fallback"] == "@sdnc"
    assert resolved_xw["logs"] == "/tmp/conn.log"
    assert resolved_xw["tags"] == {"os": "6wind"}

    n_svc = NodeClass("xw_int@dish", **resolved_xw, config=cfg)
    cmd_svc = n_svc._get_cmd()
    assert "ProxyCommand=" in cmd_svc
    assert "10.10.0.1" in cmd_svc
    assert "10.10.0.2" in cmd_svc
    assert "-J jumpconnect" not in cmd_svc

    ext_xw = cfg.getitem("xw_int@dish", extract=True)
    assert ext_xw["options"] == ""
    assert ext_xw["user"] == "federico.luzzi"
    assert ext_xw["password"] == ["dish_password"]
    n_ext = NodeClass("xw_int@dish", **ext_xw, config=cfg)
    assert n_ext._auth_candidates == n_svc._auth_candidates
    assert n_ext._get_cmd() == cmd_svc

    # Negative check: non-auth profile refs in jumphost/options/tags/logs never inherit fallback or creds
    for non_auth_node in (
        {
            "host": "10.20.0.1",
            "user": "local_user",
            "password": "local_pw",
            "jumphost": "@dish",
            "fallback": "",
        },
        {
            "host": "10.20.0.2",
            "user": "local_user",
            "password": "local_pw",
            "options": "@aws_with_fb",
            "fallback": "",
        },
        {
            "host": "10.20.0.3",
            "user": "",
            "password": "",
            "jumphost": "@dish",
            "fallback": "",
        },
    ):
        res_neg = p_svc.resolve_node_data(non_auth_node)
        assert res_neg["fallback"] == ""
        if non_auth_node["user"] == "local_user":
            n_neg = NodeClass("n_neg", **res_neg, config=cfg)
            assert [(c.user, c.is_fallback) for c in n_neg._auth_candidates] == [
                ("local_user", False)
            ]
        else:
            assert res_neg["user"] == ""
            assert res_neg["password"] == ""


def test_fallback_comma_separated_profiles_validation_parsing_resolution_and_profileused(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    import inquirer

    from connpy.cli.forms import _parse_fallback_input

    app = SimpleNamespace(
        profiles={
            "sdnc": {"user": "sdnc_u", "password": "sdnc_p"},
            "local": {"user": "local_u", "password": "local_p"},
            "p1": {"user": "u1", "password": "p1"},
            "p2": {"user": "u2", "password": "p2"},
            "p3": {"user": "u3", "password": "p3"},
            "p4": {"user": "u4", "password": "p4"},
            "p5": {"user": "u5", "password": "p5"},
            "p6": {"user": "u6", "password": "p6"},
        }
    )
    validators = Validators(app)

    assert validators.fallback_validation({}, "@sdnc, @local") is True
    assert validators.fallback_validation({}, "@sdnc,@local") is True
    assert validators.fallback_validation({}, "@sdnc") is True
    assert validators.fallback_validation({}, "@@Pass,WithComma") is True

    with pytest.raises(inquirer.errors.ValidationError) as exc_missing:
        validators.fallback_validation({}, "@sdnc, @nonexistent")
    assert "@nonexistent" in str(exc_missing.value.reason)

    with pytest.raises(inquirer.errors.ValidationError) as exc_limit:
        validators.fallback_validation({}, "@p1, @p2, @p3, @p4, @p5, @p6")
    assert str(MAX_FALLBACK_ENTRIES) in str(exc_limit.value.reason)

    with pytest.raises(inquirer.errors.ValidationError):
        validators.fallback_validation({}, "@sdnc,")

    with pytest.raises(inquirer.errors.ValidationError):
        validators.fallback_validation({}, "@sdnc, local")

    assert _parse_fallback_input("@sdnc, @local") == ["@sdnc", "@local"]
    assert _parse_fallback_input("@sdnc,@local") == ["@sdnc", "@local"]
    assert _parse_fallback_input("@sdnc") == "@sdnc"
    assert _parse_fallback_input("@@Pass,WithComma") == "@@Pass,WithComma"

    assert validate_fallback_entries("@sdnc, @local") == ["@sdnc", "@local"]
    assert validate_fallback_entries("@@Pass,WithComma") == ["@@Pass,WithComma"]
    with pytest.raises(ValueError):
        validate_fallback_entries("@sdnc,")
    with pytest.raises(ValueError):
        validate_fallback_entries("@sdnc, local")
    with pytest.raises(ValueError):
        validate_fallback_entries("@p1, @p2, @p3, @p4, @p5, @p6")

    resolved = resolve_fallback_entries("@sdnc, @local", app.profiles)
    assert resolved == [
        {"user": "sdnc_u", "password": "sdnc_p"},
        {"user": "local_u", "password": "local_p"},
    ]
    assert resolve_fallback_entries("@@Pass,WithComma", app.profiles) == [
        {"user": "", "password": "@@Pass,WithComma"}
    ]

    assert extract_profile_refs_from_value({"fallback": "@sdnc, @local"}) == {"sdnc", "local"}

    cfg = _make_config(
        tmp_path,
        {
            "profiles": {
                "sdnc": {"user": "sdnc_u", "password": "sdnc_p"},
                "local": {"user": "local_u", "password": "local_p"},
            },
            "connections": {
                "router1": {
                    "type": "connection",
                    "host": "10.0.0.1",
                    "protocol": "ssh",
                    "port": "",
                    "user": "tacacs_u",
                    "password": "tacacs_p",
                    "options": "",
                    "logs": "",
                    "tags": "",
                    "fallback": "@sdnc, @local",
                }
            },
        },
    )
    p_svc = ProfileService(cfg)
    for used_name in ("sdnc", "local"):
        with pytest.raises(InvalidConfigurationError):
            p_svc.delete_profile(used_name)
