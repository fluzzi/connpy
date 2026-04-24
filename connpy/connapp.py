#!/usr/bin/env python3
#Imports
import os
import re
import argparse
import sys
import yaml
import sys
from .core import node,nodes
from ._version import __version__
from . import printer
from .api import start_api,stop_api,debug_api
from .ai import ai

from .plugins import Plugins
from .services import (
    NodeService, ProfileService, ConfigService, 
    PluginService, AIService, SystemService,
    ExecutionService, ImportExportService, ConnpyError,
    ProfileNotFoundError, ReservedNameError
)

from rich_argparse import RichHelpFormatter
# Bridge rich-argparse with our design system
RichHelpFormatter.console = printer.console
RichHelpFormatter.styles.update({
    "argparse.args": printer.STYLES["info"],
    "argparse.groups": printer.STYLES["header"],
    "argparse.prog": printer.STYLES["pass"],
    "argparse.metavar": printer.STYLES["key"],
    "argparse.syntax": printer.STYLES["header"],
    "argparse.text": "default",
    "argparse.help": "default",
})
RichHelpFormatter.group_name_formatter = str.upper

from .cli import (
    NodeHandler, ProfileHandler, ConfigHandler, RunHandler,
    AIHandler, APIHandler, PluginHandler, ImportExportHandler,
    ContextHandler
)
from .cli.helpers import nodes_completer, folders_completer, profiles_completer
from .cli.help_text import get_help

console = printer.console

#functions and classes

class connapp:
    ''' This class starts the connection manager app. It's normally used by connection manager but you can use it on a script to run the connection manager your way and use a different configfile and key.
        '''

    def __init__(self, config):
        ''' 
            
        ### Parameters:  

            - config (obj): Object generated with configfile class, it contains
                            the nodes configuration and the methods to manage
                            the config file.

        '''
        self.config = config

        # Instantiate services
        from .services.provider import ServiceProvider
        mode = self.config.config.get("service_mode", "local")
        remote_host = self.config.config.get("remote_host", None)
        try:
            self.services = ServiceProvider(self.config, mode=mode, remote_host=remote_host)
        except ConnpyError as e:
            printer.error(f"Initialization error: {e}")
            sys.exit(1)

        self.node = node
        self.nodes = nodes
        self.start_api = start_api
        self.stop_api = stop_api # Using SystemService logic eventually
        self.debug_api = debug_api
        self.ai = ai
        
        # Register context filtering hooks
        self.services.context.config._getallnodes.register_post_hook(self.services.context.filter_node_list)
        self.services.context.config._getallfolders.register_post_hook(self.services.context.filter_node_list)
        self.services.context.config._getallnodesfull.register_post_hook(self.services.context.filter_node_dict)

        if hasattr(self.services.nodes, "list_nodes") and hasattr(self.services.nodes.list_nodes, "register_post_hook"):
            self.services.nodes.list_nodes.register_post_hook(self.services.context.filter_node_list)
        if hasattr(self.services.nodes, "list_folders") and hasattr(self.services.nodes.list_folders, "register_post_hook"):
            self.services.nodes.list_folders.register_post_hook(self.services.context.filter_node_list)

        # Populate data via services
        try:
            self.nodes_list = self.services.nodes.list_nodes()
            self.folders = self.services.nodes.list_folders()
            self.profiles = self.services.profiles.list_profiles()
            
            # Apply initial context filter to in-memory data
            self.nodes_list = self.services.context.filter_node_list(result=self.nodes_list)
            self.folders = self.services.context.filter_node_list(result=self.folders)
        except NotImplementedError:
            self.nodes_list = []
            self.folders = []
            self.profiles = []
        except ConnpyError as e:
            # If in remote mode, connectivity issues should be reported
            if mode == "remote":
                printer.warning(f"Failed to fetch data from remote server: {e}")
            self.nodes_list = []
            self.folders = []
            self.profiles = []
        except Exception as e:
            if mode == "remote":
                printer.warning(f"Unexpected error connecting to remote: {e}")
            self.nodes_list = []
            self.folders = []
            self.profiles = []
        
        # Get settings for CLI behavior from local config
        settings = self.services.config_svc.get_settings()
        self.case = settings.get("case", False)
        self.fzf = settings.get("fzf", False)
        
        from .cli.node_handler import NodeHandler
        from .cli.profile_handler import ProfileHandler
        from .cli.config_handler import ConfigHandler
        from .cli.run_handler import RunHandler
        from .cli.ai_handler import AIHandler
        from .cli.api_handler import APIHandler
        from .cli.plugin_handler import PluginHandler
        from .cli.context_handler import ContextHandler
        from .cli.import_export_handler import ImportExportHandler
        from .cli.sync_handler import SyncHandler
        
        # Instantiate Handlers
        self._node = NodeHandler(self)
        self._profile = ProfileHandler(self)
        self._config = ConfigHandler(self)
        self._run = RunHandler(self)
        self._ai = AIHandler(self)
        self._api = APIHandler(self)
        self._plugin = PluginHandler(self)
        self._context = ContextHandler(self)
        self._import_export = ImportExportHandler(self)
        self._sync = SyncHandler(self)

        # Register auto-sync hook to trigger after config saves
        from .configfile import configfile
        def auto_sync_hook(*args, **kwargs):
            self.services.sync.perform_sync(self)
            return kwargs.get("result")

        configfile._saveconfig.register_post_hook(auto_sync_hook)
        
        # Apply theme from config if exists
        user_theme = self.config.config.get("theme", {})
        self._apply_app_theme(user_theme)

    def _apply_app_theme(self, styles):
        """Unified method to apply theme to printer and help formatter."""
        active_styles = printer.apply_theme(styles)
        # Re-map help styles using the now active (potentially merged) styles
        RichHelpFormatter.styles.update({
            "argparse.args": active_styles["info"],
            "argparse.groups": active_styles["header"],
            "argparse.prog": active_styles["pass"],
            "argparse.metavar": active_styles["key"],
            "argparse.syntax": active_styles["header"],
        })

    def _service_logger(self, type, message):
        """Bridge between core services and CLI printer."""
        if type == "success":
            printer.success(message)
        elif type == "error":
            printer.error(message)
        elif type == "warning":
            printer.warning(message)
        elif type == "debug":
            printer.info(f"[DEBUG] {message}")
        elif type == "output":
            # Print raw output without tags for cleaner terminal experience
            printer.console.print(message)
        else:
            printer.info(message)

    def _custom_error(self, message):
        """Custom error handler for argparse to use the application's printer."""
        printer.error(message)
        sys.exit(2)

    def start(self,argv = sys.argv[1:]):
        ''' 
            
        ### Parameters:  

            - argv (list): List of arguments to pass to the app.
                           Default: sys.argv[1:]

        ''' 
    def get_parser(self):
        #DEFAULTPARSER
        defaultparser = argparse.ArgumentParser(prog = "connpy", description = "SSH and Telnet connection manager", formatter_class=RichHelpFormatter)
        defaultparser.error = self._custom_error
        # We add the node options to defaultparser purely so they show up in connpy --help, since 'node' is the default command.
        defaultparser.add_argument("-v","--version", dest="action", action="store_const", help="Show version", const="version", default="connect")
        defaultparser.add_argument("-a","--add", dest="action", action="store_const", help="Add new node[@subfolder][@folder] or [@subfolder]@folder", const="add", default="connect")
        defaultparser.add_argument("-r","--del", "--rm", dest="action", action="store_const", help="Delete node[@subfolder][@folder] or [@subfolder]@folder", const="del", default="connect")
        defaultparser.add_argument("-e","--mod", "--edit", dest="action", action="store_const", help="Modify node[@subfolder][@folder]", const="mod", default="connect")
        defaultparser.add_argument("-s","--show", dest="action", action="store_const", help="Show node[@subfolder][@folder]", const="show", default="connect")
        defaultparser.add_argument("-d","--debug", dest="debug", action="store_true", help="Display all conections steps")
        defaultparser.add_argument("-t","--sftp", dest="sftp", action="store_true", help="Connects using sftp instead of ssh")
        
        subparsers = defaultparser.add_subparsers(title="Commands", dest="subcommand", metavar="COMMAND")
        self.subparsers = subparsers
        #NODEPARSER
        nodeparser = subparsers.add_parser("node", help="Connect to specific node or show all matching nodes", formatter_class=RichHelpFormatter) 
        nodeparser.error = self._custom_error
        nodecrud = nodeparser.add_mutually_exclusive_group()
        nodeparser.add_argument("node", metavar="node|folder", nargs='?', default=None, action=self._store_type, help=get_help("node"))
        nodecrud.add_argument("-v","--version", dest="action", action="store_const", help="Show version", const="version", default="connect")
        nodecrud.add_argument("-a","--add", dest="action", action="store_const", help="Add new node[@subfolder][@folder] or [@subfolder]@folder", const="add", default="connect")
        nodecrud.add_argument("-r","--del", "--rm", dest="action", action="store_const", help="Delete node[@subfolder][@folder] or [@subfolder]@folder", const="del", default="connect")
        nodecrud.add_argument("-e","--mod", "--edit", dest="action", action="store_const", help="Modify node[@subfolder][@folder]", const="mod", default="connect")
        nodecrud.add_argument("-s","--show", dest="action", action="store_const", help="Show node[@subfolder][@folder]", const="show", default="connect")
        nodecrud.add_argument("-d","--debug", dest="debug", action="store_true", help="Display all conections steps")
        nodeparser.add_argument("-t","--sftp", dest="sftp", action="store_true", help="Connects using sftp instead of ssh")
        nodeparser.set_defaults(func=self._node.dispatch)
        #PROFILEPARSER
        profileparser = subparsers.add_parser("profile", help="Manage profiles", description="Manage profiles", formatter_class=RichHelpFormatter) 
        profileparser.error = self._custom_error
        profileparser.add_argument("profile", nargs=1, action=self._store_type, type=self._type_profile, help="Name of profile to manage")
        profilecrud = profileparser.add_mutually_exclusive_group(required=True)
        profilecrud.add_argument("-a", "--add", dest="action", action="store_const", help="Add new profile", const="add")
        profilecrud.add_argument("-r", "--del", "--rm", dest="action", action="store_const", help="Delete profile", const="del")
        profilecrud.add_argument("-e", "--mod", "--edit", dest="action", action="store_const", help="Modify profile", const="mod")
        profilecrud.add_argument("-s", "--show", dest="action", action="store_const", help="Show profile", const="show")
        profileparser.set_defaults(func=self._profile.dispatch)
        #MOVEPARSER
        moveparser = subparsers.add_parser("move", aliases=["mv"], help="Move node", description="Move node", formatter_class=RichHelpFormatter) 
        moveparser.error = self._custom_error
        moveparser.add_argument("move", nargs=2, action=self._store_type, help="Move node[@subfolder][@folder] dest_node[@subfolder][@folder]", default="move", type=self._type_node)
        moveparser.set_defaults(func=self._mvcp)
        #COPYPARSER
        copyparser = subparsers.add_parser("copy", aliases=["cp"], help="Copy node", description="Copy node", formatter_class=RichHelpFormatter) 
        copyparser.error = self._custom_error
        copyparser.add_argument("cp", nargs=2, action=self._store_type, help="Copy node[@subfolder][@folder] new_node[@subfolder][@folder]", default="cp", type=self._type_node)
        copyparser.set_defaults(func=self._mvcp)
        #LISTPARSER
        lsparser = subparsers.add_parser("list", aliases=["ls"], help="List profiles, nodes or folders", description="List profiles, nodes or folders", formatter_class=RichHelpFormatter) 
        lsparser.error = self._custom_error
        lsparser.add_argument("ls", action=self._store_type, choices=["profiles","nodes","folders"], help="List profiles, nodes or folders", default=False)
        lsparser.add_argument("--filter", nargs=1, help="Filter results")
        lsparser.add_argument("--format", nargs=1, help="Format of the output of nodes using {name}, {NAME}, {location}, {LOCATION}, {host} and {HOST}")
        lsparser.set_defaults(func=self._ls)
        #BULKPARSER
        bulkparser = subparsers.add_parser("bulk", help="Add nodes in bulk", description="Add nodes in bulk", formatter_class=RichHelpFormatter) 
        bulkparser.error = self._custom_error
        bulkparser.add_argument("-f", "--file", nargs=1, help="Import nodes from a file. First line nodes, second line hosts")
        bulkparser.set_defaults(func=self._import_export.bulk)
        # EXPORTPARSER
        exportparser = subparsers.add_parser("export", help="Export connection folder to YAML file", formatter_class=RichHelpFormatter) 
        exportparser.error = self._custom_error
        exportparser.add_argument("export", nargs="+", action=self._store_type, help=get_help("export")).completer = folders_completer
        exportparser.set_defaults(func=self._import_export.dispatch_export)
        # IMPORTPARSER
        importparser = subparsers.add_parser("import", help="Import connection folder from YAML file", formatter_class=RichHelpFormatter) 
        importparser.error = self._custom_error
        importparser.add_argument("file", nargs=1, action=self._store_type, help=get_help("import"))


        importparser.set_defaults(func=self._import_export.dispatch_import)
        # AIPARSER
        aiparser = subparsers.add_parser("ai", help="Make request to an AI", description="Make request to an AI", formatter_class=RichHelpFormatter) 
        aiparser.error = self._custom_error
        aiparser.add_argument("ask", nargs='*', help="Ask connpy AI something")
        aiparser.add_argument("--engineer-model", nargs=1, help="Override engineer model")
        aiparser.add_argument("--engineer-api-key", nargs=1, help="Override engineer api key")
        aiparser.add_argument("--architect-model", nargs=1, help="Override architect model")
        aiparser.add_argument("--architect-api-key", nargs=1, help="Override architect api key")
        aiparser.add_argument("--debug", action="store_true", help="Show AI reasoning and tool calls")
        aiparser.add_argument("-y", "--trust", action="store_true", help="Trust AI to execute unsafe commands without confirmation")
        aiparser.add_argument("--list", "--list-sessions", dest="list_sessions", action="store_true", help="List saved AI sessions")
        aiparser.add_argument("--session", nargs=1, help="Resume a specific AI session by ID")
        aiparser.add_argument("--resume", action="store_true", help="Resume the most recent AI session")
        aiparser.add_argument("--delete", "--delete-session", dest="delete_session", nargs=1, help="Delete an AI session by ID")
        aiparser.set_defaults(func=self._ai.dispatch)
        #RUNPARSER
        runparser = subparsers.add_parser("run", help="Run scripts or commands on nodes", description="Run scripts or commands on nodes", formatter_class=RichHelpFormatter) 
        runparser.error = self._custom_error
        runparser.add_argument("run", nargs='+', action=self._store_type, help=get_help("run"), default="run").completer = nodes_completer
        runparser.add_argument("-g","--generate", dest="action", action="store_const", help="Generate yaml file template", const="generate", default="run")
        runparser.set_defaults(func=self._run.dispatch)
        #APIPARSER
        apiparser = subparsers.add_parser("api", help="Start and stop connpy API", description="Start and stop connpy API", formatter_class=RichHelpFormatter) 
        apiparser.error = self._custom_error
        apicrud = apiparser.add_mutually_exclusive_group(required=True)
        apicrud.add_argument("-s","--start", dest="start", nargs="?", action=self._store_type, help="Start conppy api", type=int, default=8048, metavar="PORT")
        apicrud.add_argument("-r","--restart", dest="restart", nargs=0, action=self._store_type, help="Restart conppy api")
        apicrud.add_argument("-x","--stop", dest="stop", nargs=0, action=self._store_type, help="Stop conppy api")
        apicrud.add_argument("-d", "--debug", dest="debug", nargs="?", action=self._store_type, help="Run connpy server on debug mode", type=int, default=8048, metavar="PORT")
        apiparser.set_defaults(func=self._api.dispatch)
        #CONTEXTPARSER
        contextparser = subparsers.add_parser("context", help="Manage regex-based contexts", description="Manage regex-based contexts", formatter_class=RichHelpFormatter)
        contextparser.error = self._custom_error
        contextparser.add_argument("context_name", help="Name of the context", nargs='?')
        contextcrud = contextparser.add_mutually_exclusive_group(required=False)
        contextcrud.add_argument("-a", "--add", nargs='+', help='Add a new context with regex values')
        contextcrud.add_argument("-r", "--rm", "--del", dest="rm", action='store_true', help="Delete a context")
        contextcrud.add_argument("--ls", action='store_true', help="List all contexts")
        contextcrud.add_argument("--set", action='store_true', help="Set the active context")
        contextcrud.add_argument("-s", "--show", action='store_true', help="Show defined regex of a context")
        contextcrud.add_argument("-e", "--edit", "--mod", dest="edit", nargs='+', help='Modify an existing context')
        contextparser.set_defaults(func=self._context.dispatch)
        #PLUGINSPARSER
        pluginparser = subparsers.add_parser("plugin", help="Manage plugins", description="Manage plugins", formatter_class=RichHelpFormatter) 
        pluginparser.error = self._custom_error
        plugincrud = pluginparser.add_mutually_exclusive_group(required=True)
        plugincrud.add_argument("--add", metavar=("PLUGIN", "FILE"), nargs=2, help="Add new plugin")
        plugincrud.add_argument("--update", metavar=("PLUGIN", "FILE"), nargs=2, help="Update plugin")
        plugincrud.add_argument("--del", dest="delete", metavar="PLUGIN", nargs=1, help="Delete plugin")
        plugincrud.add_argument("--enable", metavar="PLUGIN", nargs=1, help="Enable plugin")
        plugincrud.add_argument("--disable", metavar="PLUGIN", nargs=1, help="Disable plugin")
        plugincrud.add_argument("--list", dest="list", action="store_true", help="List plugins")
        plugincrud.add_argument("--sync", dest="sync", action="store_true", help="Sync remote plugins cache")
        
        pluginparser.add_argument("--remote", action="store_true", help="Target remote server plugins")
        pluginparser.set_defaults(func=self._plugin.dispatch)
        #CONFIGPARSER
        configparser = subparsers.add_parser("config", help="Manage app config", description="Manage app config", formatter_class=RichHelpFormatter) 
        configparser.error = self._custom_error
        configcrud = configparser.add_mutually_exclusive_group(required=False)
        configcrud.add_argument("--allow-uppercase", dest="case", nargs=1, action=self._store_type, help="Allow case sensitive names", choices=["true","false"])
        configcrud.add_argument("--fzf", dest="fzf", nargs=1, action=self._store_type, help="Use fzf for lists", choices=["true","false"])
        configcrud.add_argument("--keepalive", dest="idletime", nargs=1, action=self._store_type, help="Set keepalive time in seconds, 0 to disable", type=int, metavar="INT")
        configcrud.add_argument("--completion", dest="completion", nargs=1, choices=["bash","zsh"], action=self._store_type, help="Get terminal completion configuration for conn")
        configcrud.add_argument("--fzf-wrapper", dest="fzf_wrapper", nargs=1, choices=["bash","zsh"], action=self._store_type, help="Get 0ms latency fzf bash/zsh wrapper")
        configcrud.add_argument("--configfolder", dest="configfolder", nargs=1, action=self._store_type, help="Set the default location for config file", metavar="FOLDER")
        configcrud.add_argument("--engineer-model", dest="engineer_model", nargs=1, action=self._store_type, help="Set engineer model", metavar="MODEL")
        configcrud.add_argument("--engineer-api-key", dest="engineer_api_key", nargs=1, action=self._store_type, help="Set engineer api_key", metavar="API_KEY")
        configcrud.add_argument("--theme", dest="theme", nargs=1, action=self._store_type, help="Set application theme (dark, light, or YAML file path)", metavar="THEME")
        configcrud.add_argument("--service-mode", dest="service_mode", nargs=1, action=self._store_type, help="Set the backend service mode (local or remote)", choices=["local", "remote"])
        configcrud.add_argument("--remote", dest="remote_host", nargs=1, action=self._store_type, help="Connect to a remote connpy service via gRPC", metavar="HOST:PORT")
        configcrud.add_argument("--architect-model", dest="architect_model", nargs=1, action=self._store_type, help="Set architect model", metavar="MODEL")
        configcrud.add_argument("--architect-api-key", dest="architect_api_key", nargs=1, action=self._store_type, help="Set architect api_key", metavar="API_KEY")
        configcrud.add_argument("--sync-remote", dest="sync_remote", nargs=1, action=self._store_type, help="Sync remote nodes to Google Drive", choices=["true","false"])
        configparser.add_argument("--trusted-commands", dest="trusted_commands", nargs=1, action=self._store_type, help="Set custom trusted commands regexes (comma separated)", metavar="REGEX,REGEX")
        configparser.set_defaults(func=self._config.dispatch)

        #SYNCPARSER
        syncparser = subparsers.add_parser("sync", help="Sync config with Google Drive", description="Sync config with Google Drive", formatter_class=RichHelpFormatter)
        syncparser.error = self._custom_error
        synccrud = syncparser.add_mutually_exclusive_group(required=True)
        synccrud.add_argument("--login", dest="action", action="store_const", const="login", help="Login to Google to enable synchronization")
        synccrud.add_argument("--logout", dest="action", action="store_const", const="logout", help="Logout from Google")
        synccrud.add_argument("--status", dest="action", action="store_const", const="status", help="Check the current status of synchronization")
        synccrud.add_argument("--list", dest="action", action="store_const", const="list", help="List all backups stored on Google")
        synccrud.add_argument("--once", dest="action", action="store_const", const="once", help="Backup current configuration to Google once")
        synccrud.add_argument("--restore", dest="action", action="store_const", const="restore", help="Restore data from Google")
        synccrud.add_argument("--start", dest="action", action="store_const", const="start", help="Enable auto-sync")
        synccrud.add_argument("--stop", dest="action", action="store_const", const="stop", help="Disable auto-sync")
        syncparser.add_argument("--id", dest="id", type=str, help="Optional file ID to restore a specific backup", required=False)
        syncparser.add_argument("--nodes", dest="restore_nodes", action="store_true", help="Restore only nodes and profiles")
        syncparser.add_argument("--config", dest="restore_config", action="store_true", help="Restore only local settings and RSA key")
        syncparser.set_defaults(func=self._sync.dispatch)

        #Add plugins

        self.plugins = Plugins()
        self.plugins._load_preferences(self.services.config_svc.get_default_dir())
        remote_enabled = (self.services.mode == "remote")
        force_sync = "--sync" in sys.argv and "plugin" in sys.argv

        try:
            core_path = os.path.dirname(os.path.realpath(__file__)) + "/core_plugins"
            self.plugins._import_plugins_to_argparse(core_path, subparsers, remote_enabled=remote_enabled)
        except Exception as e:
            printer.warning(e)
        try:
            file_path = self.services.config_svc.get_default_dir() + "/plugins"
            self.plugins._import_plugins_to_argparse(file_path, subparsers, remote_enabled=remote_enabled)
        except Exception as e:
            printer.warning(e)
            
        if remote_enabled:
            cache_dir = os.path.join(self.services.config_svc.get_default_dir(), "remote_plugins")
            try:
                self.plugins._import_remote_plugins_to_argparse(
                    self.services.plugins,
                    subparsers,
                    cache_dir,
                    force_sync=force_sync
                )
            except Exception:
                pass


        for preload in self.plugins.preloads.values():
            preload.Preload(self)

        # Update internal state and force cache generation after all preloads
        try:
            self.nodes_list = self.services.nodes.list_nodes()
            self.folders = self.services.nodes.list_folders()
            self.profiles = self.services.profiles.list_profiles()
            self.services.nodes.generate_cache(nodes=self.nodes_list, folders=self.folders, profiles=self.profiles)

            #Manage sys arguments
            self.commands = list(subparsers.choices.keys())
            self.services.nodes.set_reserved_names(self.commands)
            self.services.import_export.set_reserved_names(self.commands)
        except (NotImplementedError, ConnpyError, Exception):
            self.commands = list(subparsers.choices.keys())
            
        #Generate helps
        defaultparser.usage = get_help("usage", subparsers)
        nodeparser.help = get_help("node")
        profilecmds = []
        for action in profileparser._actions:
            profilecmds.extend(action.option_strings)
            
        return defaultparser, profilecmds

    def start(self, argv=sys.argv[1:]):
        """
        Starts the application CLI with the provided arguments.
        """
        if argv is None:
            argv = sys.argv[1:]
            
        defaultparser, profilecmds = self.get_parser()

        if len(argv) >= 2 and argv[1] == "profile" and argv[0] in profilecmds:
            argv[1] = argv[0]
            argv[0] = "profile"
        
        # Only insert default 'node' command if missing
        if len(argv) < 1 or (argv[0] not in self.commands and argv[0] not in ["-h", "--help"]):
            argv.insert(0,"node")
        args, unknown_args = defaultparser.parse_known_args(argv)
        if hasattr(args, "unknown_args"):
            args.unknown_args = unknown_args
        else:
            args = defaultparser.parse_args(argv)

        try:
            if args.subcommand in getattr(self.plugins, "remote_plugins", {}):
                import json as _json
                for chunk in self.services.plugins.invoke_plugin(args.subcommand, args):
                    if "__interact__" in chunk:
                        try:
                            data = _json.loads(chunk.strip())
                            params = data.get("__interact__")
                            if params:
                                self.services.nodes.connect_dynamic(params, debug=getattr(args, 'debug', False))
                                break
                        except (ValueError, KeyError):
                            print(chunk, end="", flush=True)
                    else:
                        print(chunk, end="", flush=True)
            elif args.subcommand in self.plugins.plugins:
                self.plugins.plugins[args.subcommand].Entrypoint(args, self.plugins.plugin_parsers[args.subcommand].parser, self)
            else:
                return args.func(args)
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)
        except KeyboardInterrupt:
            # Handle global Ctrl+C gracefully
            printer.warning("Operation cancelled by user.")
            sys.exit(130)

    class _store_type(argparse.Action):
        #Custom store type for cli app.
        def __call__(self, parser, args, values, option_string=None):
            setattr(args, "data", values)
            delattr(args,self.dest)
            setattr(args, "command", self.dest)

    def _type_node(self, arg_value, pat=re.compile(r"^[0-9a-zA-Z_.$@#-]+$")):
        if arg_value == None:
            printer.error("Missing argument node")
            sys.exit(3)
        
        # Check against reserved CLI commands
        if hasattr(self, "commands") and arg_value in self.commands:
            createrename = any(arg in ["-a", "--add", "add", "move", "mv", "copy", "cp", "bulk"] for arg in sys.argv)
            if createrename:
                printer.error(f"Argument error: '{arg_value}' is a reserved command name")
                sys.exit(2)
            
        if not pat.match(arg_value):
            printer.error(f"Argument error: {arg_value}")
            sys.exit(2)
        return arg_value
    
    def _type_profile(self, arg_value, pat=re.compile(r"^[0-9a-zA-Z_.$#-]+$")):
        if not pat.match(arg_value):
            printer.error(f"Argument error: {arg_value}")
            sys.exit(2)
        return arg_value

    def _ls(self, args):
        filter_str = args.filter[0] if args.filter else None
        format_str = args.format[0] if args.format else None
        
        try:
            if args.data == "nodes":
                items = self.services.nodes.list_nodes(filter_str, format_str)
            elif args.data == "folders":
                items = self.services.nodes.list_folders(filter_str)
            elif args.data == "profiles":
                items = self.services.profiles.list_profiles(filter_str)
            else:
                return

            if items:
                yaml_str = yaml.dump(items, sort_keys=False, default_flow_style=False)
                printer.data(args.data, yaml_str)
            else:
                msg = f"No {args.data} found"
                if filter_str:
                    msg += f" matching filter: {filter_str}"
                printer.warning(msg)
        except Exception as e:
            printer.error(str(e))

    def _mvcp(self, args):
        src, dst = args.data[0], args.data[1]
        is_copy = (args.command == "cp")
        try:
            self.services.nodes.move_node(src, dst, copy=is_copy)
            action = "moved" if not is_copy else "copied"
            printer.success(f"{src} {action} successfully to {dst}")
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)
