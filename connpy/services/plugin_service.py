from .base import BaseService
import yaml
import os
from .exceptions import InvalidConfigurationError, NodeNotFoundError


class PluginService(BaseService):
    """Business logic for enabling, disabling, and listing plugins."""

    def list_plugins(self):
        """List all core and user-defined plugins with their status and hash."""
        import os
        import hashlib
        
        # Check for user plugins directory
        plugin_dir = os.path.join(self.config.defaultdir, "plugins")
        # Check for core plugins directory
        core_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "core_plugins")
        
        all_plugin_info = {}

        def get_hash(path):
            try:
                with open(path, "rb") as f:
                    return hashlib.md5(f.read()).hexdigest()
            except Exception:
                return ""

        # User plugins
        if os.path.exists(plugin_dir):
            for f in os.listdir(plugin_dir):
                if f.endswith(".py"):
                    name = f[:-3]
                    path = os.path.join(plugin_dir, f)
                    all_plugin_info[name] = {"enabled": True, "hash": get_hash(path)}
                elif f.endswith(".py.bkp"):
                    name = f[:-7]
                    all_plugin_info[name] = {"enabled": False}

        return all_plugin_info

    def add_plugin(self, name, source_file, update=False):
        """Add or update a plugin from a local file."""
        import os
        import shutil
        from connpy.plugins import Plugins

        if not name.isalpha() or not name.islower() or len(name) > 15:
            raise InvalidConfigurationError("Plugin name should be lowercase letters up to 15 characters.")

        p_manager = Plugins()
        # Check for bad script
        error = p_manager.verify_script(source_file)
        if error:
            raise InvalidConfigurationError(f"Invalid plugin script: {error}")

        self._save_plugin_file(name, source_file, update, is_path=True)

    def add_plugin_from_bytes(self, name, content, update=False):
        """Add or update a plugin from bytes (gRPC)."""
        import tempfile
        import os
        
        if not name.isalpha() or not name.islower() or len(name) > 15:
            raise InvalidConfigurationError("Plugin name should be lowercase letters up to 15 characters.")

        # Write to temp file to verify script
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            from connpy.plugins import Plugins
            p_manager = Plugins()
            error = p_manager.verify_script(tmp_path)
            if error:
                raise InvalidConfigurationError(f"Invalid plugin script: {error}")
            
            self._save_plugin_file(name, tmp_path, update, is_path=True)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def _save_plugin_file(self, name, source, update=False, is_path=True):
        import os
        import shutil
        
        plugin_dir = os.path.join(self.config.defaultdir, "plugins")
        os.makedirs(plugin_dir, exist_ok=True)
        
        target_file = os.path.join(plugin_dir, f"{name}.py")
        backup_file = f"{target_file}.bkp"

        if not update and (os.path.exists(target_file) or os.path.exists(backup_file)):
            raise InvalidConfigurationError(f"Plugin '{name}' already exists.")

        try:
            if is_path:
                shutil.copy2(source, target_file)
            else:
                with open(target_file, "wb") as f:
                    f.write(source)
        except OSError as e:
            raise InvalidConfigurationError(f"Failed to save plugin file: {e}")

    def delete_plugin(self, name):
        """Remove a plugin file permanently."""
        import os
        plugin_file = os.path.join(self.config.defaultdir, "plugins", f"{name}.py")
        disabled_file = f"{plugin_file}.bkp"

        deleted = False
        for f in [plugin_file, disabled_file]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                    deleted = True
                except OSError as e:
                    raise InvalidConfigurationError(f"Failed to delete plugin file '{f}': {e}")
        
        if not deleted:
            raise InvalidConfigurationError(f"Plugin '{name}' not found.")

    def enable_plugin(self, name):
        """Activate a plugin by renaming its backup file."""
        import os
        plugin_file = os.path.join(self.config.defaultdir, "plugins", f"{name}.py")
        disabled_file = f"{plugin_file}.bkp"
        
        if os.path.exists(plugin_file):
            return False # Already enabled
            
        if not os.path.exists(disabled_file):
            raise InvalidConfigurationError(f"Plugin '{name}' not found.")
            
        try:
            os.rename(disabled_file, plugin_file)
            return True
        except OSError as e:
            raise InvalidConfigurationError(f"Failed to enable plugin '{name}': {e}")

    def disable_plugin(self, name):
        """Deactivate a plugin by renaming it to a backup file."""
        import os
        plugin_file = os.path.join(self.config.defaultdir, "plugins", f"{name}.py")
        disabled_file = f"{plugin_file}.bkp"
        
        if os.path.exists(disabled_file):
            return False # Already disabled
            
        if not os.path.exists(plugin_file):
            raise InvalidConfigurationError(f"Plugin '{name}' not found or is a core plugin.")
            
        try:
            os.rename(plugin_file, disabled_file)
            return True
        except OSError as e:
            raise InvalidConfigurationError(f"Failed to disable plugin '{name}': {e}")

    def get_plugin_source(self, name):
        import os
        from ..services.exceptions import InvalidConfigurationError
        
        plugin_file = os.path.join(self.config.defaultdir, "plugins", f"{name}.py")
        core_path = os.path.dirname(os.path.realpath(__file__)) + f"/../core_plugins/{name}.py"
        
        if os.path.exists(plugin_file):
            target = plugin_file
        elif os.path.exists(core_path):
            target = core_path
        else:
            raise InvalidConfigurationError(f"Plugin '{name}' not found")
        
        with open(target, "r") as f:
            return f.read()

    def invoke_plugin(self, name, args_dict):
        import sys, io
        from argparse import Namespace
        from ..services.exceptions import InvalidConfigurationError
        from connpy.plugins import Plugins
        class MockApp:
            is_mock = True
            def __init__(self, config):
                from ..core import node, nodes
                from ..ai import ai
                from ..services.provider import ServiceProvider
                
                self.config = config
                self.node = node
                self.nodes = nodes
                self.ai = ai
                
                self.services = ServiceProvider(config, mode="local")
                
                # Get settings for CLI behavior
                settings = self.services.config_svc.get_settings()
                self.case = settings.get("case", False)
                self.fzf = settings.get("fzf", False)
                
                try:
                    self.nodes_list = self.services.nodes.list_nodes()
                    self.folders = self.services.nodes.list_folders()
                    self.profiles = self.services.profiles.list_profiles()
                except Exception:
                    self.nodes_list = []
                    self.folders = []
                    self.profiles = []
        
        args = Namespace(**args_dict)
        
        p_manager = Plugins()
        import os
        plugin_file = os.path.join(self.config.defaultdir, "plugins", f"{name}.py")
        core_path = os.path.dirname(os.path.realpath(__file__)) + f"/../core_plugins/{name}.py"
        
        if os.path.exists(plugin_file):
            target = plugin_file
        elif os.path.exists(core_path):
            target = core_path
        else:
            raise InvalidConfigurationError(f"Plugin '{name}' not found")
            
        module = p_manager._import_from_path(target)
        parser = module.Parser().parser if hasattr(module, "Parser") else None
        
        if "__func_name__" in args_dict and hasattr(module, args_dict["__func_name__"]):
            args.func = getattr(module, args_dict["__func_name__"])
        
        app = MockApp(self.config)
        
        from .. import printer
        from rich.console import Console
        
        from rich.console import Console
        buf = io.StringIO()
        old_console = printer._get_console()
        old_err_console = printer._get_err_console()
        
        printer.set_thread_console(Console(file=buf, theme=printer.connpy_theme, force_terminal=True))
        printer.set_thread_err_console(Console(file=buf, theme=printer.connpy_theme, force_terminal=True))
        printer.set_thread_stream(buf)
        
        try:
            if hasattr(module, "Entrypoint"):
                module.Entrypoint(args, parser, app)
        except BaseException as e:
            if not isinstance(e, SystemExit):
                import traceback
                printer.err_console.print(traceback.format_exc())
        finally:
            printer.set_thread_console(old_console)
            printer.set_thread_err_console(old_err_console)
            printer.set_thread_stream(None)
            
        for line in buf.getvalue().splitlines(keepends=True):
            yield line
