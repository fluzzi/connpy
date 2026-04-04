"""Tests for connpy.configfile module."""
import json
import os
import re
import pytest
import yaml
from copy import deepcopy


class TestConfigfileInit:
    def test_creates_default_config(self, tmp_config_dir):
        """Creates config.yaml with defaults when it doesn't exist."""
        config_file = tmp_config_dir / "config.yaml"
        config_file.unlink(missing_ok=True)  # Remove existing
        key_file = tmp_config_dir / ".osk"

        from connpy.configfile import configfile
        conf = configfile(conf=str(config_file), key=str(key_file))

        assert config_file.exists()
        assert conf.config["case"] == False
        assert conf.config["idletime"] == 30
        assert "default" in conf.profiles

    def test_creates_rsa_key(self, tmp_config_dir):
        """Generates RSA key when it doesn't exist."""
        key_file = tmp_config_dir / ".osk"
        key_file.unlink()  # Remove existing

        from connpy.configfile import configfile
        conf = configfile(conf=str(tmp_config_dir / "config.yaml"), key=str(key_file))

        assert key_file.exists()
        assert conf.privatekey is not None
        assert conf.publickey is not None

    def test_loads_existing_config(self, config):
        """Loads correctly from existing config."""
        assert config.config is not None
        assert config.connections is not None
        assert config.profiles is not None

    def test_config_file_permissions(self, tmp_config_dir):
        """Config is created with 0o600 permissions."""
        config_file = tmp_config_dir / "config.yaml"
        config_file.unlink(missing_ok=True)

        from connpy.configfile import configfile
        configfile(conf=str(config_file), key=str(tmp_config_dir / ".osk"))

        stat = os.stat(str(config_file))
        assert oct(stat.st_mode & 0o777) == oct(0o600)

    def test_custom_paths(self, tmp_path):
        """Accepts custom paths for conf and key."""
        config_dir = tmp_path / "custom"
        config_dir.mkdir()
        (config_dir / "plugins").mkdir()

        # Write .folder for the config dir
        dot_folder = tmp_path / ".config" / "conn"
        dot_folder.mkdir(parents=True, exist_ok=True)
        (dot_folder / ".folder").write_text(str(config_dir))
        (dot_folder / "plugins").mkdir(exist_ok=True)

        conf_path = str(config_dir / "my_config.yaml")
        key_path = str(config_dir / "my_key")

        from connpy.configfile import configfile
        conf = configfile(conf=conf_path, key=key_path)

        assert conf.file == conf_path
        assert conf.key == key_path


class TestEncryption:
    def test_encrypt_password(self, config):
        """Encrypts and produces b'...' format."""
        encrypted = config.encrypt("mysecret")
        assert encrypted.startswith("b'") or encrypted.startswith('b"')

    def test_encrypt_decrypt_roundtrip(self, config):
        """Encrypt then decrypt returns original."""
        from Crypto.PublicKey import RSA
        from Crypto.Cipher import PKCS1_OAEP
        import ast

        original = "super_secret_password"
        encrypted = config.encrypt(original)

        # Decrypt
        with open(config.key) as f:
            key = RSA.import_key(f.read())
        decryptor = PKCS1_OAEP.new(key)
        decrypted = decryptor.decrypt(ast.literal_eval(encrypted)).decode("utf-8")
        assert decrypted == original


class TestExplodeUnique:
    def test_simple_node(self, config):
        result = config._explode_unique("router1")
        assert result == {"id": "router1"}

    def test_node_with_folder(self, config):
        result = config._explode_unique("r1@office")
        assert result == {"id": "r1", "folder": "office"}

    def test_node_with_subfolder(self, config):
        result = config._explode_unique("r1@dc@office")
        assert result == {"id": "r1", "folder": "office", "subfolder": "dc"}

    def test_folder_only(self, config):
        result = config._explode_unique("@office")
        assert result == {"folder": "office"}

    def test_subfolder_only(self, config):
        result = config._explode_unique("@dc@office")
        assert result == {"folder": "office", "subfolder": "dc"}

    def test_too_deep(self, config):
        result = config._explode_unique("a@b@c@d")
        assert result == False

    def test_empty_folder(self, config):
        result = config._explode_unique("a@")
        assert result == False

    def test_empty_subfolder(self, config):
        result = config._explode_unique("a@@office")
        assert result == False


class TestCRUDNodes:
    def test_add_node_root(self, config):
        config._connections_add(
            id="router1", host="10.0.0.1", protocol="ssh",
            port="22", user="admin", password="pass", options="",
            logs="", tags="", jumphost=""
        )
        assert "router1" in config.connections
        assert config.connections["router1"]["host"] == "10.0.0.1"

    def test_add_node_folder(self, config):
        config._folder_add(folder="office")
        config._connections_add(
            id="server1", folder="office", host="10.0.1.1",
            protocol="ssh", port="", user="root", password="pass",
            options="", logs="", tags="", jumphost=""
        )
        assert "server1" in config.connections["office"]

    def test_add_node_subfolder(self, config):
        config._folder_add(folder="office")
        config._folder_add(folder="office", subfolder="dc")
        config._connections_add(
            id="db1", folder="office", subfolder="dc", host="10.0.2.1",
            protocol="ssh", port="", user="dbadmin", password="pass",
            options="", logs="", tags="", jumphost=""
        )
        assert "db1" in config.connections["office"]["dc"]

    def test_del_node_root(self, config):
        config._connections_add(
            id="router1", host="10.0.0.1", protocol="ssh",
            port="", user="", password="", options="",
            logs="", tags="", jumphost=""
        )
        config._connections_del(id="router1")
        assert "router1" not in config.connections

    def test_del_node_folder(self, config):
        config._folder_add(folder="office")
        config._connections_add(
            id="server1", folder="office", host="10.0.1.1",
            protocol="ssh", port="", user="", password="",
            options="", logs="", tags="", jumphost=""
        )
        config._connections_del(id="server1", folder="office")
        assert "server1" not in config.connections["office"]

    def test_add_folder(self, config):
        config._folder_add(folder="office")
        assert "office" in config.connections
        assert config.connections["office"]["type"] == "folder"

    def test_add_subfolder(self, config):
        config._folder_add(folder="office")
        config._folder_add(folder="office", subfolder="dc")
        assert "dc" in config.connections["office"]
        assert config.connections["office"]["dc"]["type"] == "subfolder"

    def test_del_folder(self, config):
        config._folder_add(folder="office")
        config._folder_del(folder="office")
        assert "office" not in config.connections

    def test_del_subfolder(self, config):
        config._folder_add(folder="office")
        config._folder_add(folder="office", subfolder="dc")
        config._folder_del(folder="office", subfolder="dc")
        assert "dc" not in config.connections["office"]


class TestCRUDProfiles:
    def test_add_profile(self, config):
        config._profiles_add(
            id="myprofile", host="", protocol="telnet",
            port="23", user="user1", password="pass1",
            options="", logs="", tags="", jumphost=""
        )
        assert "myprofile" in config.profiles
        assert config.profiles["myprofile"]["protocol"] == "telnet"

    def test_del_profile(self, config):
        config._profiles_add(
            id="temp", host="", protocol="ssh", port="",
            user="", password="", options="", logs="", tags="", jumphost=""
        )
        config._profiles_del(id="temp")
        assert "temp" not in config.profiles

    def test_default_profile_exists(self, config):
        assert "default" in config.profiles


class TestGetItem:
    def test_getitem_node(self, populated_config):
        node = populated_config.getitem("router1")
        assert node["host"] == "10.0.0.1"
        assert "type" not in node  # type is stripped

    def test_getitem_folder(self, populated_config):
        nodes = populated_config.getitem("@office")
        # Should contain server1@office but NOT datacenter (subfolder)
        assert "server1@office" in nodes
        assert all("type" not in v for v in nodes.values())

    def test_getitem_subfolder(self, populated_config):
        nodes = populated_config.getitem("@datacenter@office")
        assert "db1@datacenter@office" in nodes

    def test_getitem_node_in_folder(self, populated_config):
        node = populated_config.getitem("server1@office")
        assert node["host"] == "10.0.1.1"

    def test_getitem_node_in_subfolder(self, populated_config):
        node = populated_config.getitem("db1@datacenter@office")
        assert node["host"] == "10.0.2.1"

    def test_getitem_with_profile_extraction(self, tmp_config_dir):
        """extract=True resolves @profile references."""
        config_file = tmp_config_dir / "config.yaml"
        data = {
            "config": {"case": False, "idletime": 30, "fzf": False},
            "connections": {
                "router1": {
                    "host": "10.0.0.1", "protocol": "ssh", "port": "",
                    "user": "@office-user", "password": "@office-user",
                    "options": "", "logs": "", "tags": "", "jumphost": "",
                    "type": "connection"
                }
            },
            "profiles": {
                "default": {"host": "", "protocol": "ssh", "port": "",
                           "user": "", "password": "", "options": "",
                           "logs": "", "tags": "", "jumphost": ""},
                "office-user": {"host": "", "protocol": "ssh", "port": "",
                               "user": "officeadmin", "password": "officepass",
                               "options": "", "logs": "", "tags": "", "jumphost": ""}
            }
        }
        config_file.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))

        from connpy.configfile import configfile
        conf = configfile(conf=str(config_file), key=str(tmp_config_dir / ".osk"))

        node = conf.getitem("router1", extract=True)
        assert node["user"] == "officeadmin"
        assert node["password"] == "officepass"

    def test_getitems_multiple(self, populated_config):
        nodes = populated_config.getitems(["router1", "server1@office"])
        assert "router1" in nodes
        assert "server1@office" in nodes

    def test_getitems_folder(self, populated_config):
        nodes = populated_config.getitems(["@office"])
        assert "server1@office" in nodes


class TestGetAll:
    def test_getallnodes_no_filter(self, populated_config):
        nodes = populated_config._getallnodes()
        assert "router1" in nodes
        assert "server1@office" in nodes
        assert "db1@datacenter@office" in nodes

    def test_getallnodes_string_filter(self, populated_config):
        nodes = populated_config._getallnodes("router.*")
        assert "router1" in nodes
        assert "server1@office" not in nodes

    def test_getallnodes_list_filter(self, populated_config):
        nodes = populated_config._getallnodes(["router.*", "db.*"])
        assert "router1" in nodes
        assert "db1@datacenter@office" in nodes
        assert "server1@office" not in nodes

    def test_getallnodes_filter_invalid_type(self, populated_config):
        with pytest.raises(ValueError):
            populated_config._getallnodes(123)

    def test_getallfolders(self, populated_config):
        folders = populated_config._getallfolders()
        assert "@office" in folders
        assert "@datacenter@office" in folders

    def test_getallnodesfull(self, populated_config):
        nodes = populated_config._getallnodesfull()
        assert "router1" in nodes
        assert nodes["router1"]["host"] == "10.0.0.1"

    def test_getallnodesfull_with_filter(self, populated_config):
        nodes = populated_config._getallnodesfull("router.*")
        assert "router1" in nodes
        assert "server1@office" not in nodes

    def test_profileused(self, tmp_config_dir):
        """Detects nodes using a specific profile."""
        config_file = tmp_config_dir / "config.yaml"
        data = {
            "config": {"case": False, "idletime": 30, "fzf": False},
            "connections": {
                "router1": {
                    "host": "10.0.0.1", "protocol": "ssh", "port": "",
                    "user": "@myprofile", "password": "pass",
                    "options": "", "logs": "", "tags": "", "jumphost": "",
                    "type": "connection"
                },
                "router2": {
                    "host": "10.0.0.2", "protocol": "ssh", "port": "",
                    "user": "admin", "password": "pass",
                    "options": "", "logs": "", "tags": "", "jumphost": "",
                    "type": "connection"
                }
            },
            "profiles": {
                "default": {"host": "", "protocol": "ssh", "port": "",
                           "user": "", "password": "", "options": "",
                           "logs": "", "tags": "", "jumphost": ""},
                "myprofile": {"host": "", "protocol": "ssh", "port": "",
                             "user": "profuser", "password": "profpass",
                             "options": "", "logs": "", "tags": "", "jumphost": ""}
            }
        }
        config_file.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        from connpy.configfile import configfile
        conf = configfile(conf=str(config_file), key=str(tmp_config_dir / ".osk"))

        used = conf._profileused("myprofile")
        assert "router1" in used
        assert "router2" not in used

    def test_saveconfig(self, config):
        """Save and reload correctly."""
        config._connections_add(
            id="test_node", host="1.2.3.4", protocol="ssh",
            port="", user="", password="", options="",
            logs="", tags="", jumphost=""
        )
        result = config._saveconfig(config.file)
        assert result == 0

        # Reload and verify
        from connpy.configfile import configfile
        reloaded = configfile(conf=config.file, key=config.key)
        assert "test_node" in reloaded.connections


class TestValidateConfig:
    def test_valid_config(self, config):
        data = {"config": {}, "connections": {}, "profiles": {}}
        assert config._validate_config(data) == True

    def test_none_data(self, config):
        assert config._validate_config(None) == False

    def test_string_data(self, config):
        assert config._validate_config("not a dict") == False

    def test_missing_key(self, config):
        assert config._validate_config({"config": {}, "connections": {}}) == False

    def test_empty_dict(self, config):
        assert config._validate_config({}) == False


class TestCorruptionRecovery:
    def test_corrupt_yaml_recovers_from_cache(self, tmp_config_dir):
        """If YAML is corrupt but cache is valid, recovers from cache."""
        config_file = tmp_config_dir / "config.yaml"
        key_file = tmp_config_dir / ".osk"

        # Write valid config with router1
        valid_data = {
            "config": {"case": False, "idletime": 30, "fzf": False},
            "connections": {"router1": {"host": "10.0.0.1", "type": "connection", "protocol": "ssh", "port": "", "user": "", "password": "", "options": "", "logs": "", "tags": "", "jumphost": ""}},
            "profiles": {"default": {"host": "", "protocol": "ssh", "port": "", "user": "", "password": "", "options": "", "logs": "", "tags": "", "jumphost": ""}}
        }
        config_file.write_text(yaml.dump(valid_data, default_flow_style=False, sort_keys=False))

        from connpy.configfile import configfile
        conf = configfile(conf=str(config_file), key=str(key_file))
        # Save to populate cache at the real self.cachefile path
        conf._saveconfig(conf.file)
        cachefile_path = conf.cachefile
        assert os.path.exists(cachefile_path)

        # Now corrupt the YAML
        config_file.write_text("")
        import time; time.sleep(0.05)  # Ensure YAML is newer than cache

        # Reload - should recover from cache
        conf2 = configfile(conf=str(config_file), key=str(key_file))
        assert "router1" in conf2.connections
        assert conf2.connections["router1"]["host"] == "10.0.0.1"

    def test_corrupt_cache_uses_yaml(self, tmp_config_dir):
        """If cache is corrupt but YAML is valid, uses YAML."""
        config_file = tmp_config_dir / "config.yaml"
        key_file = tmp_config_dir / ".osk"

        valid_data = {
            "config": {"case": False, "idletime": 30, "fzf": False},
            "connections": {},
            "profiles": {"default": {"host": "", "protocol": "ssh", "port": "", "user": "", "password": "", "options": "", "logs": "", "tags": "", "jumphost": ""}}
        }
        config_file.write_text(yaml.dump(valid_data, default_flow_style=False, sort_keys=False))

        from connpy.configfile import configfile
        conf = configfile(conf=str(config_file), key=str(key_file))
        cachefile_path = conf.cachefile

        # Now corrupt the cache (valid JSON but invalid config structure)
        from pathlib import Path
        Path(cachefile_path).write_text(json.dumps({"garbage": True}))
        # Make cache newer than YAML to force cache path
        import time; time.sleep(0.05)
        os.utime(cachefile_path, None)

        conf2 = configfile(conf=str(config_file), key=str(key_file))
        assert conf2.config["case"] == False
        assert "default" in conf2.profiles

    def test_both_corrupt_creates_default(self, tmp_config_dir):
        """If both YAML and cache are corrupt, creates fresh config."""
        config_file = tmp_config_dir / "config.yaml"
        key_file = tmp_config_dir / ".osk"

        from connpy.configfile import configfile
        conf = configfile(conf=str(config_file), key=str(key_file))
        cachefile_path = conf.cachefile

        # Corrupt YAML
        config_file.write_text("")
        # Corrupt cache
        from pathlib import Path
        Path(cachefile_path).write_text(json.dumps({"garbage": True}))
        import time; time.sleep(0.05)
        os.utime(str(config_file), None)

        conf2 = configfile(conf=str(config_file), key=str(key_file))

        # Should get defaults, not crash
        assert conf2.config is not None
        assert "default" in conf2.profiles
        assert isinstance(conf2.connections, dict)


class TestAtomicSave:
    def test_save_creates_no_leftover_tmp(self, config):
        """After successful save, no .tmp file remains."""
        config._connections_add(
            id="test123", host="1.2.3.4", protocol="ssh",
            port="", user="", password="", options="",
            logs="", tags="", jumphost=""
        )
        result = config._saveconfig(config.file)
        assert result == 0
        assert not os.path.exists(config.file + '.tmp')

    def test_save_preserves_original_on_error(self, config):
        """If save fails, original config file is not corrupted."""
        import unittest.mock as mock

        config._connections_add(
            id="original_node", host="10.0.0.1", protocol="ssh",
            port="", user="", password="", options="",
            logs="", tags="", jumphost=""
        )
        config._saveconfig(config.file)

        # Now add another node and make yaml.dump fail
        config._connections_add(
            id="new_node", host="10.0.0.2", protocol="ssh",
            port="", user="", password="", options="",
            logs="", tags="", jumphost=""
        )

        with mock.patch('connpy.configfile.yaml.dump', side_effect=IOError("disk full")):
            result = config._saveconfig(config.file)
            assert result == 1

        # Original file should still be valid with original_node
        from connpy.configfile import configfile
        reloaded = configfile(conf=config.file, key=config.key)
        assert "original_node" in reloaded.connections


class TestMigrationSafety:
    def test_migration_validates_legacy_data(self, tmp_path):
        """Migration skips invalid legacy JSON files."""
        from unittest.mock import patch
        config_dir = tmp_path / ".config" / "conn"
        config_dir.mkdir(parents=True)
        (config_dir / "plugins").mkdir()

        # Write .folder
        (config_dir / ".folder").write_text(str(config_dir))

        # Generate RSA key
        from Crypto.PublicKey import RSA
        key = RSA.generate(2048)
        key_file = config_dir / ".osk"
        key_file.write_bytes(key.export_key("PEM"))
        os.chmod(str(key_file), 0o600)

        # Write invalid JSON config (missing required keys)
        legacy_file = config_dir / "config.json"
        legacy_file.write_text(json.dumps({"garbage": True}))

        with patch("os.path.expanduser", return_value=str(tmp_path)):
            from connpy.configfile import configfile
            conf = configfile(key=str(key_file))

        # Legacy file should NOT have been moved to .backup
        assert legacy_file.exists()
        assert not (config_dir / "config.json.backup").exists()

    def test_migration_verifies_written_yaml(self, tmp_path):
        """Migration succeeds when legacy JSON is valid."""
        from unittest.mock import patch
        config_dir = tmp_path / ".config" / "conn"
        config_dir.mkdir(parents=True)
        (config_dir / "plugins").mkdir()

        # Write .folder
        (config_dir / ".folder").write_text(str(config_dir))

        # Generate RSA key
        from Crypto.PublicKey import RSA
        key = RSA.generate(2048)
        key_file = config_dir / ".osk"
        key_file.write_bytes(key.export_key("PEM"))
        os.chmod(str(key_file), 0o600)

        valid_data = {
            "config": {"case": False, "idletime": 30, "fzf": False},
            "connections": {"r1": {"host": "1.2.3.4", "type": "connection", "protocol": "ssh", "port": "", "user": "", "password": "", "options": "", "logs": "", "tags": "", "jumphost": ""}},
            "profiles": {"default": {"host": "", "protocol": "ssh", "port": "", "user": "", "password": "", "options": "", "logs": "", "tags": "", "jumphost": ""}}
        }
        legacy_file = config_dir / "config.json"
        legacy_file.write_text(json.dumps(valid_data))

        with patch("os.path.expanduser", return_value=str(tmp_path)):
            from connpy.configfile import configfile
            conf = configfile(key=str(key_file))

        # Migration should have succeeded: YAML exists, JSON backed up
        yaml_file = config_dir / "config.yaml"
        assert yaml_file.exists()
        assert (config_dir / "config.json.backup").exists()
        assert not legacy_file.exists()
        assert "r1" in conf.connections
