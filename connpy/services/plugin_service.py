from .base import BaseService
import yaml
import os
from .exceptions import InvalidConfigurationError, NodeNotFoundError


class PluginService(BaseService):
    """Business logic for enabling, disabling, and listing plugins."""

    def _get_plugin_path(self, name, include_disabled=True):
        """Resolves the physical path of a plugin by name. Priority: user, shared/global, core."""
        import os
        
        # 1. User directory
        user_dir = os.path.join(self.config.defaultdir, "plugins")
        if os.path.exists(user_dir):
            p_file = os.path.join(user_dir, f"{name}.py")
            if os.path.exists(p_file):
                return p_file, "user", True
            if include_disabled:
                bkp_file = os.path.join(user_dir, f"{name}.py.bkp")
                if os.path.exists(bkp_file):
                    return bkp_file, "user", False
                    
        # 2. Shared/Global directory
        if hasattr(self.config, "_shared_config") and self.config._shared_config:
            shared_dir = os.path.join(self.config._shared_config.defaultdir, "plugins")
            if os.path.exists(shared_dir):
                p_file = os.path.join(shared_dir, f"{name}.py")
                if os.path.exists(p_file):
                    return p_file, "shared", True
                if include_disabled:
                    bkp_file = os.path.join(shared_dir, f"{name}.py.bkp")
                    if os.path.exists(bkp_file):
                        return bkp_file, "shared", False
                        
        # 3. Core plugins
        core_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "core_plugins")
        p_file = os.path.join(core_dir, f"{name}.py")
        if os.path.exists(p_file):
            return p_file, "core", True
            
        return None, None, False


    def list_plugins(self):
        """List all core and user-defined plugins with their status and hash."""
        import os
        import hashlib
        
        all_plugin_info = {}

        def get_hash(path):
            try:
                with open(path, "rb") as f:
                    return hashlib.md5(f.read()).hexdigest()
            except Exception:
                return ""

        # 1. Scan core plugins (lowest priority)
        core_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "core_plugins")
        if os.path.exists(core_dir):
            for f in os.listdir(core_dir):
                if f.endswith(".py"):
                    name = f[:-3]
                    path = os.path.join(core_dir, f)
                    all_plugin_info[name] = {"enabled": True, "hash": get_hash(path), "origin": "core"}

        # 2. Scan shared plugins (medium priority)
        if hasattr(self.config, "_shared_config") and self.config._shared_config:
            shared_dir = os.path.join(self.config._shared_config.defaultdir, "plugins")
            if os.path.exists(shared_dir):
                for f in os.listdir(shared_dir):
                    if f.endswith(".py"):
                        name = f[:-3]
                        path = os.path.join(shared_dir, f)
                        all_plugin_info[name] = {"enabled": True, "hash": get_hash(path), "origin": "shared"}
                    elif f.endswith(".py.bkp"):
                        name = f[:-7]
                        all_plugin_info[name] = {"enabled": False, "origin": "shared"}

        # 3. Scan user plugins (highest priority)
        user_dir = os.path.join(self.config.defaultdir, "plugins")
        if os.path.exists(user_dir):
            for f in os.listdir(user_dir):
                if f.endswith(".py"):
                    name = f[:-3]
                    path = os.path.join(user_dir, f)
                    all_plugin_info[name] = {"enabled": True, "hash": get_hash(path), "origin": "user"}
                elif f.endswith(".py.bkp"):
                    name = f[:-7]
                    all_plugin_info[name] = {"enabled": False, "origin": "user"}

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
            # If not deleted from user directory, check if it's in shared or core
            path, origin, enabled = self._get_plugin_path(name, include_disabled=True)
            if origin in ["shared", "core"]:
                raise InvalidConfigurationError("Global and core plugins are read-only and cannot be deleted by users.")
            raise InvalidConfigurationError(f"Plugin '{name}' not found.")

    def enable_plugin(self, name):
        """Activate a plugin by renaming its backup file."""
        import os
        plugin_file = os.path.join(self.config.defaultdir, "plugins", f"{name}.py")
        disabled_file = f"{plugin_file}.bkp"
        
        if os.path.exists(disabled_file):
            # Check if it is a shadow bkp file (0 bytes shadowing shared/core)
            is_shadow = False
            if os.path.getsize(disabled_file) == 0:
                # Resolve without the local bkp file to verify if shared/core has it
                path, origin, enabled = self._get_plugin_path(name, include_disabled=False)
                if origin in ["shared", "core"]:
                    is_shadow = True
            
            if is_shadow:
                # Remove shadow file to restore inheritance
                try:
                    os.remove(disabled_file)
                    return True
                except OSError as e:
                    raise InvalidConfigurationError(f"Failed to remove shadow file '{disabled_file}': {e}")
            else:
                try:
                    os.rename(disabled_file, plugin_file)
                    return True
                except OSError as e:
                    raise InvalidConfigurationError(f"Failed to enable plugin '{name}': {e}")
        
        if os.path.exists(plugin_file):
            return False # Already enabled
            
        # If it doesn't exist locally, check if it's already an active shared/core plugin
        path, origin, enabled = self._get_plugin_path(name, include_disabled=False)
        if origin in ["shared", "core"]:
            return False # Already active/enabled through inheritance
            
        raise InvalidConfigurationError(f"Plugin '{name}' not found.")

    def disable_plugin(self, name):
        """Deactivate a plugin by renaming it to a backup file."""
        import os
        plugin_file = os.path.join(self.config.defaultdir, "plugins", f"{name}.py")
        disabled_file = f"{plugin_file}.bkp"
        
        if os.path.exists(plugin_file):
            # Regular user-level plugin exists. Rename to bkp
            try:
                os.rename(plugin_file, disabled_file)
                return True
            except OSError as e:
                raise InvalidConfigurationError(f"Failed to disable plugin '{name}': {e}")
                
        if os.path.exists(disabled_file):
            return False # Already disabled
            
        # Check if it exists in shared or core
        path, origin, enabled = self._get_plugin_path(name, include_disabled=False)
        if origin in ["shared", "core"]:
            # Shadow disable it by creating an empty .py.bkp in user plugins dir
            plugin_dir = os.path.dirname(plugin_file)
            os.makedirs(plugin_dir, exist_ok=True)
            try:
                with open(disabled_file, "w") as f:
                    f.write("")
                return True
            except OSError as e:
                raise InvalidConfigurationError(f"Failed to create shadow disable file: {e}")
                
        raise InvalidConfigurationError(f"Plugin '{name}' not found or is already disabled.")

    def get_plugin_source(self, name):
        import os
        from ..services.exceptions import InvalidConfigurationError
        
        path, origin, enabled = self._get_plugin_path(name, include_disabled=False)
        if not path:
            raise InvalidConfigurationError(f"Plugin '{name}' not found")
        
        with open(path, "r") as f:
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
        
        path, origin, enabled = self._get_plugin_path(name, include_disabled=False)
        if not path:
            raise InvalidConfigurationError(f"Plugin '{name}' not found")
            
        module = p_manager._import_from_path(path)
        parser = module.Parser().parser if hasattr(module, "Parser") else None
        
        if "__func_name__" in args_dict and hasattr(module, args_dict["__func_name__"]):
            args.func = getattr(module, args_dict["__func_name__"])
        
        app = MockApp(self.config)
        
        from .. import printer
        from rich.console import Console
        
        from rich.console import Console
        import queue
        import threading
        
        q = queue.Queue()
        
        class QueueIO(io.StringIO):
            def write(self, s):
                q.put(s)
                return len(s)
            def flush(self):
                pass
                
        buf = QueueIO()
        old_console = printer._get_console()
        old_err_console = printer._get_err_console()
        
        def run_plugin():
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
                q.put(None)
                
        t = threading.Thread(target=run_plugin, daemon=True)
        t.start()
        
        while True:
            item = q.get()
            if item is None:
                break
            yield item
