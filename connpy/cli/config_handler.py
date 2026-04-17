import sys
import yaml
from .. import printer
from ..services.exceptions import ConnpyError, InvalidConfigurationError
from .help_text import get_instructions

class ConfigHandler:
    def __init__(self, app):
        self.app = app

    def dispatch(self, args):
        actions = {
            "completion": self.show_completion,
            "fzf_wrapper": self.show_fzf_wrapper,
            "case": self.set_case,
            "fzf": self.set_fzf,
            "idletime": self.set_idletime,
            "configfolder": self.set_configfolder,
            "theme": self.set_theme,
            "engineer_model": self.set_ai_config,
            "engineer_api_key": self.set_ai_config,
            "architect_model": self.set_ai_config,
            "architect_api_key": self.set_ai_config,
            "trusted_commands": self.set_ai_config,
            "service_mode": self.set_service_mode,
            "remote_host": self.set_remote_host,
            "sync_remote": self.set_sync_remote
        }
        handler = actions.get(getattr(args, "command", None))
        if handler:
            return handler(args)
        
        # If no specific command was triggered, show current configuration
        return self.show_config(args)

    def show_config(self, args):
        settings = self.app.services.config_svc.get_settings()
        yaml_str = yaml.dump(settings, sort_keys=False, default_flow_style=False)
        printer.data("Current Configuration", yaml_str)

    def set_service_mode(self, args):
        new_mode = args.data[0]
        if new_mode == "remote":
            settings = self.app.services.config_svc.get_settings()
            if not settings.get("remote_host"):
                printer.error("Remote host must be configured before switching to remote mode")
                return
        
        self.app.services.config_svc.update_setting("service_mode", new_mode)
        
        # Immediate sync of fzf/text cache files for the new mode
        try:
            # 1. Clear old cache files to avoid discrepancies if fetch fails
            self.app.config._generate_nodes_cache(nodes=[], folders=[], profiles=[])
            
            # 2. Re-initialize services for the new mode
            from ..services.provider import ServiceProvider
            settings = self.app.services.config_svc.get_settings()
            new_services = ServiceProvider(self.app.config, mode=new_mode, remote_host=settings.get("remote_host"))
            
            # 3. Fetch data from new mode and generate cache
            nodes = new_services.nodes.list_nodes()
            folders = new_services.nodes.list_folders()
            profiles = new_services.profiles.list_profiles()
            new_services.nodes.generate_cache(nodes=nodes, folders=folders, profiles=profiles)
            
            printer.success("Config saved")
        except Exception as e:
            printer.success("Config saved")
            printer.warning(f"Note: Could not synchronize fzf cache: {e}")


    def set_remote_host(self, args):
        self.app.services.config_svc.update_setting("remote_host", args.data[0])
        printer.success("Config saved")

    def set_theme(self, args):
        try:
            valid_styles = self.app.services.config_svc.apply_theme_from_file(args.data[0])
            # Apply immediately to current session
            printer.apply_theme(valid_styles)
            printer.success(f"Theme '{args.data[0]}' applied and saved")
        except (ConnpyError, InvalidConfigurationError) as e:
            printer.error(str(e))

    def show_fzf_wrapper(self, args):
        print(get_instructions("fzf_wrapper_" + args.data[0]))

    def show_completion(self, args):
        print(get_instructions(args.data[0] + "completion"))

    def set_case(self, args):
        val = (args.data[0].lower() == "true")
        self.app.services.config_svc.update_setting("case", val)
        self.app.case = val
        printer.success("Config saved")

    def set_fzf(self, args):
        val = (args.data[0].lower() == "true")
        self.app.services.config_svc.update_setting("fzf", val)
        self.app.fzf = val
        printer.success("Config saved")

    def set_idletime(self, args):
        try:
            val = max(0, int(args.data[0]))
            self.app.services.config_svc.update_setting("idletime", val)
            printer.success("Config saved")
        except ValueError:
            printer.error("Keepalive must be an integer.")

    def set_configfolder(self, args):
        try:
            self.app.services.config_svc.set_config_folder(args.data[0])
            printer.success("Config saved")
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)

    def set_sync_remote(self, args):
        val = (args.data[0].lower() == "true")
        self.app.services.config_svc.update_setting("sync_remote", val)
        self.app.services.sync.sync_remote = val
        printer.success("Config saved")

    def set_ai_config(self, args):
        try:
            settings = self.app.services.config_svc.get_settings()
            aiconfig = settings.get("ai", {})
            aiconfig[args.command] = args.data[0]
            self.app.services.config_svc.update_setting("ai", aiconfig)
            printer.success("Config saved")
        except ConnpyError as e:
            printer.error(str(e))

