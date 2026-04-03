"""Shared fixtures for connpy tests.

All tests use tmp_path to create isolated config/keys.
No test touches ~/.config/conn/
"""
import pytest
import json
import os
from unittest.mock import patch, MagicMock
from Crypto.PublicKey import RSA


# ---------------------------------------------------------------------------
# Minimal config data
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "config": {"case": False, "idletime": 30, "fzf": False},
    "connections": {},
    "profiles": {
        "default": {
            "host": "", "protocol": "ssh", "port": "", "user": "",
            "password": "", "options": "", "logs": "", "tags": "", "jumphost": ""
        }
    }
}

SAMPLE_CONNECTIONS = {
    "router1": {
        "host": "10.0.0.1", "protocol": "ssh", "port": "22",
        "user": "admin", "password": "pass1", "options": "",
        "logs": "", "tags": "", "jumphost": "", "type": "connection"
    },
    "office": {
        "type": "folder",
        "server1": {
            "host": "10.0.1.1", "protocol": "ssh", "port": "",
            "user": "root", "password": "pass2", "options": "",
            "logs": "", "tags": "", "jumphost": "", "type": "connection"
        },
        "datacenter": {
            "type": "subfolder",
            "db1": {
                "host": "10.0.2.1", "protocol": "ssh", "port": "",
                "user": "dbadmin", "password": "pass3", "options": "",
                "logs": "", "tags": "", "jumphost": "", "type": "connection"
            }
        }
    }
}

SAMPLE_PROFILES = {
    "default": {
        "host": "", "protocol": "ssh", "port": "", "user": "",
        "password": "", "options": "", "logs": "", "tags": "", "jumphost": ""
    },
    "office-user": {
        "host": "", "protocol": "ssh", "port": "", "user": "officeadmin",
        "password": "officepass", "options": "", "logs": "", "tags": "", "jumphost": ""
    }
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create an isolated config directory with config.json and RSA key."""
    config_dir = tmp_path / ".config" / "conn"
    config_dir.mkdir(parents=True)
    plugins_dir = config_dir / "plugins"
    plugins_dir.mkdir()

    # Write config.json
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps(DEFAULT_CONFIG, indent=4))
    os.chmod(str(config_file), 0o600)

    # Write .folder (points to itself)
    folder_file = config_dir / ".folder"
    folder_file.write_text(str(config_dir))

    # Generate RSA key
    key = RSA.generate(2048)
    key_file = config_dir / ".osk"
    key_file.write_bytes(key.export_key("PEM"))
    os.chmod(str(key_file), 0o600)

    return config_dir


@pytest.fixture
def config(tmp_config_dir):
    """Create a configfile instance pointing to tmp directory."""
    from connpy.configfile import configfile
    conf_path = str(tmp_config_dir / "config.json")
    key_path = str(tmp_config_dir / ".osk")
    return configfile(conf=conf_path, key=key_path)


@pytest.fixture
def populated_config(tmp_config_dir):
    """Create a configfile with sample nodes/profiles pre-loaded."""
    config_file = tmp_config_dir / "config.json"
    data = {
        "config": {"case": False, "idletime": 30, "fzf": False},
        "connections": SAMPLE_CONNECTIONS,
        "profiles": SAMPLE_PROFILES
    }
    config_file.write_text(json.dumps(data, indent=4))
    from connpy.configfile import configfile
    return configfile(conf=str(config_file), key=str(tmp_config_dir / ".osk"))


@pytest.fixture
def mock_pexpect():
    """Mock pexpect.spawn for connection tests."""
    with patch("connpy.core.pexpect") as mock_pexp:
        child = MagicMock()
        child.before = b""
        child.after = b"router#"
        child.readline.return_value = b""
        child.child_fd = 3
        mock_pexp.spawn.return_value = child
        mock_pexp.EOF = object()
        mock_pexp.TIMEOUT = object()

        # Also mock fdpexpect
        with patch("connpy.core.fdpexpect", create=True) as mock_fd:
            mock_fd.fdspawn.return_value = MagicMock()
            yield {
                "pexpect": mock_pexp,
                "child": child,
                "fdpexpect": mock_fd
            }


@pytest.fixture
def mock_litellm():
    """Mock litellm.completion for AI tests."""
    with patch("connpy.ai.completion") as mock_comp:
        # Create a default response
        msg = MagicMock()
        msg.content = "Test response from AI"
        msg.tool_calls = None
        msg.role = "assistant"
        msg.model_dump.return_value = {
            "role": "assistant",
            "content": "Test response from AI"
        }

        choice = MagicMock()
        choice.message = msg

        response = MagicMock()
        response.choices = [choice]
        response.usage = MagicMock()
        response.usage.prompt_tokens = 100
        response.usage.completion_tokens = 50
        response.usage.total_tokens = 150

        mock_comp.return_value = response

        yield {
            "completion": mock_comp,
            "response": response,
            "message": msg,
            "choice": choice
        }


@pytest.fixture
def ai_config(tmp_config_dir):
    """Create a configfile with AI keys configured for AI tests."""
    config_file = tmp_config_dir / "config.json"
    data = {
        "config": {
            "case": False, "idletime": 30, "fzf": False,
            "ai": {
                "engineer_model": "test/test-model",
                "engineer_api_key": "test-engineer-key",
                "architect_model": "test/test-architect",
                "architect_api_key": "test-architect-key"
            }
        },
        "connections": SAMPLE_CONNECTIONS,
        "profiles": SAMPLE_PROFILES
    }
    config_file.write_text(json.dumps(data, indent=4))
    from connpy.configfile import configfile
    return configfile(conf=str(config_file), key=str(tmp_config_dir / ".osk"))
