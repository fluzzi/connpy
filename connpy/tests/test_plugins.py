"""Tests for connpy.plugins module."""
import os
import textwrap
import pytest
from connpy.plugins import Plugins


# ---------------------------------------------------------------------------
# Helper: write a plugin script to a file
# ---------------------------------------------------------------------------
def _write_plugin(path, code):
    """Write dedented code to a file."""
    with open(path, "w") as f:
        f.write(textwrap.dedent(code))


# =========================================================================
# verify_script tests
# =========================================================================

class TestVerifyScript:
    def test_valid_parser_entrypoint(self, tmp_path):
        p = tmp_path / "good.py"
        _write_plugin(p, """\
            import argparse

            class Parser:
                def __init__(self):
                    self.parser = argparse.ArgumentParser()

            class Entrypoint:
                def __init__(self, args, parser, connapp):
                    pass
        """)
        plugins = Plugins()
        assert plugins.verify_script(str(p)) == False

    def test_valid_preload_only(self, tmp_path):
        p = tmp_path / "preload.py"
        _write_plugin(p, """\
            class Preload:
                def __init__(self, connapp):
                    pass
        """)
        plugins = Plugins()
        assert plugins.verify_script(str(p)) == False

    def test_valid_all_three(self, tmp_path):
        p = tmp_path / "all.py"
        _write_plugin(p, """\
            import argparse

            class Parser:
                def __init__(self):
                    self.parser = argparse.ArgumentParser()

            class Entrypoint:
                def __init__(self, args, parser, connapp):
                    pass

            class Preload:
                def __init__(self, connapp):
                    pass
        """)
        plugins = Plugins()
        assert plugins.verify_script(str(p)) == False

    def test_parser_without_entrypoint(self, tmp_path):
        p = tmp_path / "bad.py"
        _write_plugin(p, """\
            import argparse

            class Parser:
                def __init__(self):
                    self.parser = argparse.ArgumentParser()
        """)
        plugins = Plugins()
        result = plugins.verify_script(str(p))
        assert result  # Should be a truthy error string
        assert "Entrypoint" in result

    def test_entrypoint_without_parser(self, tmp_path):
        p = tmp_path / "bad.py"
        _write_plugin(p, """\
            class Entrypoint:
                def __init__(self, args, parser, connapp):
                    pass
        """)
        plugins = Plugins()
        result = plugins.verify_script(str(p))
        assert result
        assert "Parser" in result

    def test_no_valid_class(self, tmp_path):
        p = tmp_path / "empty.py"
        _write_plugin(p, """\
            def some_function():
                pass
        """)
        plugins = Plugins()
        result = plugins.verify_script(str(p))
        assert result
        assert "No valid class" in result

    def test_parser_missing_self_parser(self, tmp_path):
        p = tmp_path / "bad.py"
        _write_plugin(p, """\
            class Parser:
                def __init__(self):
                    self.something = "not parser"

            class Entrypoint:
                def __init__(self, args, parser, connapp):
                    pass
        """)
        plugins = Plugins()
        result = plugins.verify_script(str(p))
        assert result
        assert "self.parser" in result

    def test_entrypoint_wrong_args(self, tmp_path):
        p = tmp_path / "bad.py"
        _write_plugin(p, """\
            import argparse

            class Parser:
                def __init__(self):
                    self.parser = argparse.ArgumentParser()

            class Entrypoint:
                def __init__(self, args):
                    pass
        """)
        plugins = Plugins()
        result = plugins.verify_script(str(p))
        assert result
        assert "Entrypoint" in result

    def test_preload_wrong_args(self, tmp_path):
        p = tmp_path / "bad.py"
        _write_plugin(p, """\
            class Preload:
                def __init__(self, connapp, extra):
                    pass
        """)
        plugins = Plugins()
        result = plugins.verify_script(str(p))
        assert result
        assert "Preload" in result

    def test_disallowed_top_level(self, tmp_path):
        p = tmp_path / "bad.py"
        _write_plugin(p, """\
            MY_GLOBAL = "not allowed"

            class Preload:
                def __init__(self, connapp):
                    pass
        """)
        plugins = Plugins()
        result = plugins.verify_script(str(p))
        assert result
        assert "not allowed" in result.lower() or "Plugin can only have" in result

    def test_syntax_error(self, tmp_path):
        p = tmp_path / "bad.py"
        _write_plugin(p, """\
            def broken(
        """)
        plugins = Plugins()
        result = plugins.verify_script(str(p))
        assert result
        assert "Syntax error" in result

    def test_if_name_main_allowed(self, tmp_path):
        p = tmp_path / "good.py"
        _write_plugin(p, """\
            class Preload:
                def __init__(self, connapp):
                    pass

            if __name__ == "__main__":
                print("standalone")
        """)
        plugins = Plugins()
        assert plugins.verify_script(str(p)) == False

    def test_other_if_not_allowed(self, tmp_path):
        p = tmp_path / "bad.py"
        _write_plugin(p, """\
            import sys

            if sys.platform == "linux":
                pass

            class Preload:
                def __init__(self, connapp):
                    pass
        """)
        plugins = Plugins()
        result = plugins.verify_script(str(p))
        assert result
        assert "__name__" in result


# =========================================================================
# Import and loading tests
# =========================================================================

class TestPluginLoading:
    def test_import_from_path(self, tmp_path):
        p = tmp_path / "mymod.py"
        _write_plugin(p, """\
            MY_VAR = 42
        """)
        plugins = Plugins()
        module = plugins._import_from_path(str(p))
        assert module.MY_VAR == 42

    def test_import_plugins_to_argparse(self, tmp_path):
        """Valid plugins get loaded into argparse."""
        import argparse

        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        _write_plugin(plugin_dir / "myplugin.py", """\
            import argparse

            class Parser:
                def __init__(self):
                    self.parser = argparse.ArgumentParser(description="My plugin")

            class Entrypoint:
                def __init__(self, args, parser, connapp):
                    pass
        """)

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()

        plugins = Plugins()
        plugins._import_plugins_to_argparse(str(plugin_dir), subparsers)

        assert "myplugin" in plugins.plugins
        assert "myplugin" in plugins.plugin_parsers

    def test_plugin_name_collision(self, tmp_path):
        """Plugin with same name as existing subcommand is skipped."""
        import argparse

        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        _write_plugin(plugin_dir / "existcmd.py", """\
            import argparse

            class Parser:
                def __init__(self):
                    self.parser = argparse.ArgumentParser()

            class Entrypoint:
                def __init__(self, args, parser, connapp):
                    pass
        """)

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()
        subparsers.add_parser("existcmd")  # Already taken

        plugins = Plugins()
        plugins._import_plugins_to_argparse(str(plugin_dir), subparsers)

        assert "existcmd" not in plugins.plugins

    def test_preload_registration(self, tmp_path):
        """Preload class gets registered in preloads dict."""
        import argparse

        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        _write_plugin(plugin_dir / "preloader.py", """\
            class Preload:
                def __init__(self, connapp):
                    pass
        """)

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()

        plugins = Plugins()
        plugins._import_plugins_to_argparse(str(plugin_dir), subparsers)

        assert "preloader" in plugins.preloads

    def test_invalid_plugin_skipped(self, tmp_path, capsys):
        """Invalid plugin is skipped with error message."""
        import argparse

        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        _write_plugin(plugin_dir / "badplugin.py", """\
            MY_GLOBAL = "bad"
        """)

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()

        plugins = Plugins()
        plugins._import_plugins_to_argparse(str(plugin_dir), subparsers)

        assert "badplugin" not in plugins.plugins
        captured = capsys.readouterr()
        assert "Failed to load plugin" in captured.err or "Failed to load plugin" in captured.out

    def test_empty_directory(self, tmp_path):
        """Empty directory doesn't cause errors."""
        import argparse

        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()

        plugins = Plugins()
        plugins._import_plugins_to_argparse(str(plugin_dir), subparsers)

        assert len(plugins.plugins) == 0
