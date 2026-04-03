"""Tests for connpy.configfile module."""
import json
import os
import re
import pytest
from copy import deepcopy


class TestConfigfileInit:
    def test_creates_default_config(self, tmp_config_dir):
        """Creates config.json with defaults when it doesn't exist."""
        config_file = tmp_config_dir / "config.json"
        config_file.unlink()  # Remove existing
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
        conf = configfile(conf=str(tmp_config_dir / "config.json"), key=str(key_file))

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
        config_file = tmp_config_dir / "config.json"
        config_file.unlink()

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

        conf_path = str(config_dir / "my_config.json")
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
        config_file = tmp_config_dir / "config.json"
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
        config_file.write_text(json.dumps(data, indent=4))

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
        config_file = tmp_config_dir / "config.json"
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
        config_file.write_text(json.dumps(data, indent=4))
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
