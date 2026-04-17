import sys
import yaml
from .. import printer
from ..services.exceptions import ConnpyError

class PluginHandler:
    def __init__(self, app):
        self.app = app

    def dispatch(self, args):
        try:
            # We determine the target PluginService/PluginStub based on standard 'mode'
            # But wait, local plugins should go to app.services._init_local version
            # Or we can just use the provided app.services.plugins and pass the appropriate grpc calls if needed.
            
            is_remote = getattr(args, "remote", False)
            if is_remote and self.app.services.mode != "remote":
                printer.error("Cannot use --remote flag when not running in remote mode.")
                return

            if args.add:
                self.app.services.plugins.add_plugin(args.add[0], args.add[1])
                printer.success(f"Plugin {args.add[0]} added successfully{' remotely' if is_remote else ''}.")
            elif args.update:
                self.app.services.plugins.add_plugin(args.update[0], args.update[1], update=True)
                printer.success(f"Plugin {args.update[0]} updated successfully{' remotely' if is_remote else ''}.")
            elif args.delete:
                self.app.services.plugins.delete_plugin(args.delete[0])
                printer.success(f"Plugin {args.delete[0]} deleted successfully{' remotely' if is_remote else ''}.")
            elif args.enable:
                name = args.enable[0]
                if is_remote:
                    self.app.plugins.preferences[name] = "remote"
                else:
                    if name in self.app.plugins.preferences:
                        del self.app.plugins.preferences[name]
                
                self.app.plugins._save_preferences(self.app.services.config_svc.get_default_dir())
                
                # Always try to enable it locally (remove .bkp) if it exists
                # regardless of mode, to keep files consistent with "enabled" state
                try:
                    # We use a local service instance to ensure we touch local files
                    from ..services.plugin_service import PluginService
                    local_svc = PluginService(self.app.services.config)
                    local_svc.enable_plugin(name)
                except Exception:
                    pass # Ignore if not found locally or already enabled

                if is_remote and self.app.services.mode == "remote":
                    self.app.services.plugins.enable_plugin(name)
                        
                printer.success(f"Plugin {name} enabled successfully{' remotely' if is_remote else ' locally'}.")
            elif args.disable:
                name = args.disable[0]
                success = False
                if is_remote:
                    if self.app.services.mode == "remote":
                        self.app.services.plugins.disable_plugin(name)
                        success = True
                else:
                    # Disable locally
                    from ..services.plugin_service import PluginService
                    local_svc = PluginService(self.app.services.config)
                    try:
                        if local_svc.disable_plugin(name):
                            success = True
                    except Exception as e:
                        printer.warning(f"Could not disable local plugin: {e}")
                
                if success:
                    printer.success(f"Plugin {name} disabled successfully{' remotely' if is_remote else ' locally'}.")
            
            # If any remote operation was performed, trigger a sync to update local cache immediately
            if is_remote and self.app.services.mode == "remote":
                try:
                    import os
                    cache_dir = os.path.join(self.app.services.config_svc.get_default_dir(), "remote_plugins")
                    # We use a dummy subparser choice check bypass by passing force_sync=True
                    # or just letting the hasher handle it.
                    self.app.plugins._import_remote_plugins_to_argparse(
                        self.app.services.plugins,
                        self.app.subparsers, # We'll need to make sure this is available
                        cache_dir,
                        force_sync=True
                    )
                except Exception:
                    pass

            elif getattr(args, "sync", False):
                # The actual sync logic is performed in connapp.py during init
                # if the --sync flag is detected in sys.argv
                printer.success("Remote plugins synchronized successfully.")
            elif args.list:
                # We need to fetch both local and remote if in remote mode
                local_plugins = {}
                remote_plugins = {}
                
                # Fetch depending on mode
                if self.app.services.mode == "remote":
                    # For local we need to instantiate a local plugin service bypassing stub
                    from ..services.plugin_service import PluginService
                    local_svc = PluginService(self.app.services.config)
                    local_plugins = local_svc.list_plugins()
                    remote_plugins = self.app.services.plugins.list_plugins()
                else:
                    local_plugins = self.app.services.plugins.list_plugins()

                from rich.table import Table
                
                table = Table(title="Available Plugins", show_header=True, header_style="bold cyan")
                table.add_column("Plugin", style="cyan")
                table.add_column("State", style="bold")
                table.add_column("Origin", style="magenta")

                # Populate local plugins
                for name, details in local_plugins.items():
                    state = "Disabled" if not details.get("enabled", True) else "Active"
                    color = "red" if state == "Disabled" else "green"
                    
                    if self.app.services.mode == "remote" and state == "Active":
                        if self.app.plugins.preferences.get(name) == "remote":
                            state = "Shadowed (Override by Remote)"
                            color = "yellow"
                    
                    table.add_row(name, f"[{color}]{state}[/{color}]", "Local")

                # Populate remote plugins
                if self.app.services.mode == "remote":
                    for name, details in remote_plugins.items():
                        state = "Disabled" if not details.get("enabled", True) else "Active"
                        color = "red" if state == "Disabled" else "green"
                        
                        if state == "Active":
                            pref = self.app.plugins.preferences.get(name, "local")
                            # If preference isn't remote and the plugin exists locally, local takes priority
                            if pref != "remote" and name in local_plugins:
                                state = "Shadowed (Override by Local)"
                                color = "yellow"
                                
                        table.add_row(name, f"[{color}]{state}[/{color}]", "Remote")

                if not local_plugins and not remote_plugins:
                    printer.console.print("  No plugins found.")
                else:
                    printer.console.print(table)

        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)
