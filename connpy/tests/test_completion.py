"""Tests for connpy.completion module."""
import os
import json
import pytest
from connpy.completion import load_txt_cache, get_cwd


# =========================================================================
# load_txt_cache tests
# =========================================================================

class TestLoadTxtCache:
    def test_load_existing_cache(self, tmp_path):
        """Loads lines from a file correctly."""
        cache_file = tmp_path / "cache.txt"
        cache_file.write_text("node1\nnode2\nnode3@folder")
        
        result = load_txt_cache(str(cache_file))
        assert result == ["node1", "node2", "node3@folder"]

    def test_load_nonexistent_cache(self, tmp_path):
        """Returns empty list if file is missing."""
        result = load_txt_cache(str(tmp_path / "missing.txt"))
        assert result == []


# =========================================================================
# get_cwd tests
# =========================================================================

class TestGetCwd:
    def test_current_dir(self, tmp_path, monkeypatch):
        """Lists files in current directory."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "file1.txt").touch()
        (tmp_path / "file2.py").touch()
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        result = get_cwd(["run", "run"])
        # Should list files
        assert any("file1.txt" in r for r in result)
        assert any("subdir/" in r for r in result)

    def test_specific_path(self, tmp_path, monkeypatch):
        """Lists files matching a partial path."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "script.yaml").touch()
        (tmp_path / "script2.yaml").touch()

        result = get_cwd(["run", "script"])
        assert any("script" in r for r in result)

    def test_folder_only(self, tmp_path, monkeypatch):
        """folderonly=True returns only directories."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "file.txt").touch()
        subdir = tmp_path / "mydir"
        subdir.mkdir()

        result = get_cwd(["export", "export"], folderonly=True)
        files_in_result = [r for r in result if "file.txt" in r]
        assert len(files_in_result) == 0
        dirs_in_result = [r for r in result if "mydir" in r]
        assert len(dirs_in_result) > 0


# =========================================================================
# Tree completions tests
# =========================================================================

class TestTreeCompletions:
    def test_config_auth_completions(self):
        from connpy.completion import _build_tree, resolve_completion
        tree = _build_tree([], [], [], {}, "/tmp")
        # Test config completions
        config_completions = resolve_completion(["config", ""], tree)
        assert "--engineer-auth" in config_completions
        assert "--architect-auth" in config_completions

        # Resolve when --engineer-auth is chosen in config
        auth_comp = resolve_completion(["config", "--engineer-auth", ""], tree)
        assert isinstance(auth_comp, list)

        # Loop back check:
        # e.g., connpy config --engineer-auth some_val
        # should loop back and resolve to config options
        loop_back_comp = resolve_completion(["config", "--engineer-auth", "some_val", ""], tree)
        assert "--architect-auth" in loop_back_comp
        assert "--engineer-auth" in loop_back_comp

    def test_ai_auth_completions(self):
        from connpy.completion import _build_tree, resolve_completion
        tree = _build_tree([], [], [], {}, "/tmp")
        # Test ai completions
        ai_completions = resolve_completion(["ai", ""], tree)
        assert "--engineer-auth" in ai_completions
        assert "--architect-auth" in ai_completions

        # Resolve after choosing option
        auth_comp = resolve_completion(["ai", "--engineer-auth", ""], tree)
        assert isinstance(auth_comp, list)

        # Loop back check:
        # e.g., connpy ai --engineer-auth some_val
        # should loop back and resolve to ai options, excluding --engineer-auth
        loop_back_comp = resolve_completion(["ai", "--engineer-auth", "some_val", ""], tree)
        assert "--architect-auth" in loop_back_comp
        assert "--engineer-auth" not in loop_back_comp

    def test_sixwindmcp_plugin_completions(self):
        from connpy.completion import resolve_completion, get_cwd
        import importlib.util
        
        # Load the testremote/remote_plugins/sixwindmcp.py plugin
        plugin_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "testremote", "remote_plugins", "sixwindmcp.py"
        )
        spec = importlib.util.spec_from_file_location("sixwindmcp", plugin_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.get_cwd = get_cwd
        
        plugin_node = module._connpy_tree()
        assert "--set-path" in plugin_node
        assert "--path" in plugin_node
        assert "start" in plugin_node
        
        tree = {"sixwindmcp": plugin_node}
        
        # Test resolution when --set-path is chosen
        res = resolve_completion(["sixwindmcp", "--set-path", ""], tree)
        assert isinstance(res, list)
        
        # Loop back check:
        # e.g., connpy sixwindmcp --set-path /tmp start
        # should loop back and resolve to plugin options
        loop_back_comp = resolve_completion(["sixwindmcp", "--set-path", "/tmp", ""], tree)
        assert "start" in loop_back_comp
        assert "stop" in loop_back_comp


class TestUserCompletions:
    def test_user_command_options(self):
        from connpy.completion import _build_tree, resolve_completion
        tree = _build_tree([], [], [], {}, "/tmp")
        
        # Test options at the "user" level
        user_completions = resolve_completion(["user", ""], tree)
        assert "--add" in user_completions
        assert "--del" in user_completions
        assert "--rm" in user_completions
        assert "--show" in user_completions
        assert "--regen-password" in user_completions
        assert "--list" in user_completions
        assert "--ls" in user_completions

    def test_user_action_completed_users(self, tmp_path):
        from connpy.completion import _build_tree, resolve_completion
        import yaml
        
        # Create users directory and mock registry
        users_dir = tmp_path / "users"
        users_dir.mkdir()
        registry_file = users_dir / "registry.yaml"
        
        registry_data = {
            "users": {
                "fluzzi": {"password_hash": "hash1"},
                "john": {"password_hash": "hash2"}
            }
        }
        with open(registry_file, "w") as f:
            yaml.dump(registry_data, f)
            
        tree = _build_tree([], [], [], {}, str(tmp_path))
        
        # Resolve after --del, --rm, --show, --regen-password
        for action in ["--del", "--rm", "--show", "--regen-password"]:
            completions = resolve_completion(["user", action, ""], tree)
            assert "fluzzi" in completions
            assert "john" in completions
            
        # --add username completed options
        add_completions = resolve_completion(["user", "--add", "newguy", ""], tree)
        assert "--path" in add_completions

    def test_login_logout_completions(self):
        from connpy.completion import _build_tree, resolve_completion
        tree = _build_tree([], [], [], {}, "/tmp")
        
        # Test login option resolution
        login_completions = resolve_completion(["login", ""], tree)
        assert "--help" in login_completions
        
        # Test logout option resolution
        logout_completions = resolve_completion(["logout", ""], tree)
        assert "--help" in logout_completions



