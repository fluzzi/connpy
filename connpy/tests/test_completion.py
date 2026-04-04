"""Tests for connpy.completion module."""
import os
import json
import pytest
from connpy.completion import load_txt_cache, _getcwd, _get_plugins


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
# _getcwd tests
# =========================================================================

class TestGetCwd:
    def test_current_dir(self, tmp_path, monkeypatch):
        """Lists files in current directory."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "file1.txt").touch()
        (tmp_path / "file2.py").touch()
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        result = _getcwd(["run", "run"], "run")
        # Should list files
        assert any("file1.txt" in r for r in result)
        assert any("subdir/" in r for r in result)

    def test_specific_path(self, tmp_path, monkeypatch):
        """Lists files matching a partial path."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "script.yaml").touch()
        (tmp_path / "script2.yaml").touch()

        result = _getcwd(["run", "script"], "run")
        assert any("script" in r for r in result)

    def test_folder_only(self, tmp_path, monkeypatch):
        """folderonly=True returns only directories."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "file.txt").touch()
        subdir = tmp_path / "mydir"
        subdir.mkdir()

        result = _getcwd(["export", "export"], "export", folderonly=True)
        files_in_result = [r for r in result if "file.txt" in r]
        assert len(files_in_result) == 0
        dirs_in_result = [r for r in result if "mydir" in r]
        assert len(dirs_in_result) > 0


# =========================================================================
# _get_plugins tests
# =========================================================================

class TestGetPlugins:
    def test_get_plugins_disable(self, tmp_path):
        """--disable returns enabled plugins."""
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "active.py").touch()
        (plugin_dir / "disabled.py.bkp").touch()

        result = _get_plugins("--disable", str(tmp_path))
        assert "active" in result
        assert "disabled" not in result

    def test_get_plugins_enable(self, tmp_path):
        """--enable returns disabled plugins."""
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "active.py").touch()
        (plugin_dir / "disabled.py.bkp").touch()

        result = _get_plugins("--enable", str(tmp_path))
        assert "disabled" in result
        assert "active" not in result

    def test_get_plugins_del(self, tmp_path):
        """--del returns all plugins."""
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "active.py").touch()
        (plugin_dir / "disabled.py.bkp").touch()

        result = _get_plugins("--del", str(tmp_path))
        assert "active" in result
        assert "disabled" in result

    def test_get_plugins_all(self, tmp_path):
        """'all' returns dict with paths."""
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "myplugin.py").touch()

        result = _get_plugins("all", str(tmp_path))
        assert isinstance(result, dict)
        assert "myplugin" in result

    def test_get_plugins_empty_dir(self, tmp_path):
        """Empty plugins directory returns empty list."""
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()

        result = _get_plugins("--disable", str(tmp_path))
        assert result == []
