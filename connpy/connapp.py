#!/usr/bin/env python3
#Imports
import os
import re
import ast
import argparse
import sys
import inquirer
from .core import node,nodes
from ._version import __version__
from .api import start_api,stop_api,debug_api,app
from .ai import ai
from .plugins import Plugins
import yaml
import shutil
class NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True
import ast
from rich import print as mdprint
from rich.markdown import Markdown
try:
    from pyfzf.pyfzf import FzfPrompt
except:
    FzfPrompt = None



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
        self.app = app
        self.node = node
        self.nodes = nodes
        self.start_api = start_api
        self.stop_api = stop_api
        self.debug_api = debug_api
        self.ai = ai
        self.config = config
        self.nodes_list = self.config._getallnodes()
        self.folders = self.config._getallfolders()
        self.profiles = list(self.config.profiles.keys())
        self.case = self.config.config["case"]
        try:
            self.fzf = self.config.config["fzf"]
        except:
            self.fzf = False


    def start(self,argv = sys.argv[1:]):
        ''' 
            
        ### Parameters:  

            - argv (list): List of arguments to pass to the app.
                           Default: sys.argv[1:]

        ''' 
        #DEFAULTPARSER
        defaultparser = argparse.ArgumentParser(prog = "conn", description = "SSH and Telnet connection manager", formatter_class=argparse.RawTextHelpFormatter)
        subparsers = defaultparser.add_subparsers(title="Commands", dest="subcommand")
        #NODEPARSER
        nodeparser = subparsers.add_parser("node", formatter_class=argparse.RawTextHelpFormatter) 
        nodecrud = nodeparser.add_mutually_exclusive_group()
        nodeparser.add_argument("node", metavar="node|folder", nargs='?', default=None, action=self._store_type, help=self._help("node"))
        nodecrud.add_argument("-v","--version", dest="action", action="store_const", help="Show version", const="version", default="connect")
        nodecrud.add_argument("-a","--add", dest="action", action="store_const", help="Add new node[@subfolder][@folder] or [@subfolder]@folder", const="add", default="connect")
        nodecrud.add_argument("-r","--del", "--rm", dest="action", action="store_const", help="Delete node[@subfolder][@folder] or [@subfolder]@folder", const="del", default="connect")
        nodecrud.add_argument("-e","--mod", "--edit", dest="action", action="store_const", help="Modify node[@subfolder][@folder]", const="mod", default="connect")
        nodecrud.add_argument("-s","--show", dest="action", action="store_const", help="Show node[@subfolder][@folder]", const="show", default="connect")
        nodecrud.add_argument("-d","--debug", dest="debug", action="store_true", help="Display all conections steps")
        nodeparser.add_argument("-t","--sftp", dest="sftp", action="store_true", help="Connects using sftp instead of ssh")
        nodeparser.set_defaults(func=self._func_node)
        #PROFILEPARSER
        profileparser = subparsers.add_parser("profile", description="Manage profiles") 
        profileparser.add_argument("profile", nargs=1, action=self._store_type, type=self._type_profile, help="Name of profile to manage")
        profilecrud = profileparser.add_mutually_exclusive_group(required=True)
        profilecrud.add_argument("-a", "--add", dest="action", action="store_const", help="Add new profile", const="add")
        profilecrud.add_argument("-r", "--del", "--rm", dest="action", action="store_const", help="Delete profile", const="del")
        profilecrud.add_argument("-e", "--mod", "--edit", dest="action", action="store_const", help="Modify profile", const="mod")
        profilecrud.add_argument("-s", "--show", dest="action", action="store_const", help="Show profile", const="show")
        profileparser.set_defaults(func=self._func_profile)
        #MOVEPARSER
        moveparser = subparsers.add_parser("move", aliases=["mv"], description="Move node") 
        moveparser.add_argument("move", nargs=2, action=self._store_type, help="Move node[@subfolder][@folder] dest_node[@subfolder][@folder]", default="move", type=self._type_node)
        moveparser.set_defaults(func=self._func_others)
        #COPYPARSER
        copyparser = subparsers.add_parser("copy", aliases=["cp"], description="Copy node") 
        copyparser.add_argument("cp", nargs=2, action=self._store_type, help="Copy node[@subfolder][@folder] new_node[@subfolder][@folder]", default="cp", type=self._type_node)
        copyparser.set_defaults(func=self._func_others)
        #LISTPARSER
        lsparser = subparsers.add_parser("list", aliases=["ls"], description="List profiles, nodes or folders") 
        lsparser.add_argument("ls", action=self._store_type, choices=["profiles","nodes","folders"], help="List profiles, nodes or folders", default=False)
        lsparser.add_argument("--filter", nargs=1, help="Filter results")
        lsparser.add_argument("--format", nargs=1, help="Format of the output of nodes using {name}, {NAME}, {location}, {LOCATION}, {host} and {HOST}")
        lsparser.set_defaults(func=self._func_others)
        #BULKPARSER
        bulkparser = subparsers.add_parser("bulk", description="Add nodes in bulk") 
        bulkparser.add_argument("bulk", const="bulk", nargs=0, action=self._store_type, help="Add nodes in bulk")
        bulkparser.set_defaults(func=self._func_others)
        # EXPORTPARSER
        exportparser = subparsers.add_parser("export", description="Export connection folder to Yaml file") 
        exportparser.add_argument("export", nargs="+", action=self._store_type, help="Export /path/to/file.yml [@subfolder1][@folder1] [@subfolderN][@folderN]")
        exportparser.set_defaults(func=self._func_export)
        # IMPORTPARSER
        importparser = subparsers.add_parser("import", description="Import connection folder to config from Yaml file") 
        importparser.add_argument("file", nargs=1, action=self._store_type, help="Import /path/to/file.yml")
        importparser.set_defaults(func=self._func_import)
        # AIPARSER
        aiparser = subparsers.add_parser("ai", description="Make request to an AI") 
        aiparser.add_argument("ask", nargs='*', help="Ask connpy AI something")
        aiparser.add_argument("--model", nargs=1, help="Set the OPENAI model id")
        aiparser.add_argument("--org", nargs=1, help="Set the OPENAI organization id")
        aiparser.add_argument("--api_key", nargs=1, help="Set the OPENAI API key")
        aiparser.set_defaults(func=self._func_ai)
        #RUNPARSER
        runparser = subparsers.add_parser("run", description="Run scripts or commands on nodes", formatter_class=argparse.RawTextHelpFormatter) 
        runparser.add_argument("run", nargs='+', action=self._store_type, help=self._help("run"), default="run")
        runparser.add_argument("-g","--generate", dest="action", action="store_const", help="Generate yaml file template", const="generate", default="run")
        runparser.set_defaults(func=self._func_run)
        #APIPARSER
        apiparser = subparsers.add_parser("api", description="Start and stop connpy api") 
        apicrud = apiparser.add_mutually_exclusive_group(required=True)
        apicrud.add_argument("-s","--start", dest="start", nargs="?", action=self._store_type, help="Start conppy api", type=int, default=8048, metavar="PORT")
        apicrud.add_argument("-r","--restart", dest="restart", nargs=0, action=self._store_type, help="Restart conppy api")
        apicrud.add_argument("-x","--stop", dest="stop", nargs=0, action=self._store_type, help="Stop conppy api")
        apicrud.add_argument("-d", "--debug", dest="debug", nargs="?", action=self._store_type, help="Run connpy server on debug mode", type=int, default=8048, metavar="PORT")
        apiparser.set_defaults(func=self._func_api)
        #PLUGINSPARSER
        pluginparser = subparsers.add_parser("plugin", description="Manage plugins") 
        plugincrud = pluginparser.add_mutually_exclusive_group(required=True)
        plugincrud.add_argument("--add", metavar=("PLUGIN", "FILE"), nargs=2, help="Add new plugin")
        plugincrud.add_argument("--update", metavar=("PLUGIN", "FILE"), nargs=2, help="Update plugin")
        plugincrud.add_argument("--del", dest="delete", metavar="PLUGIN", nargs=1, help="Delete plugin")
        plugincrud.add_argument("--enable", metavar="PLUGIN", nargs=1, help="Enable plugin")
        plugincrud.add_argument("--disable", metavar="PLUGIN", nargs=1, help="Disable plugin")
        plugincrud.add_argument("--list", dest="list", action="store_true", help="Disable plugin")
        pluginparser.set_defaults(func=self._func_plugin)
        #CONFIGPARSER
        configparser = subparsers.add_parser("config", description="Manage app config") 
        configcrud = configparser.add_mutually_exclusive_group(required=True)
        configcrud.add_argument("--allow-uppercase", dest="case", nargs=1, action=self._store_type, help="Allow case sensitive names", choices=["true","false"])
        configcrud.add_argument("--fzf", dest="fzf", nargs=1, action=self._store_type, help="Use fzf for lists", choices=["true","false"])
        configcrud.add_argument("--keepalive", dest="idletime", nargs=1, action=self._store_type, help="Set keepalive time in seconds, 0 to disable", type=int, metavar="INT")
        configcrud.add_argument("--completion", dest="completion", nargs=1, choices=["bash","zsh"], action=self._store_type, help="Get terminal completion configuration for conn")
        configcrud.add_argument("--configfolder", dest="configfolder", nargs=1, action=self._store_type, help="Set the default location for config file", metavar="FOLDER")
        configcrud.add_argument("--openai-org", dest="organization", nargs=1, action=self._store_type, help="Set openai organization", metavar="ORGANIZATION")
        configcrud.add_argument("--openai-api-key", dest="api_key", nargs=1, action=self._store_type, help="Set openai api_key", metavar="API_KEY")
        configcrud.add_argument("--openai-model", dest="model", nargs=1, action=self._store_type, help="Set openai model", metavar="MODEL")
        configparser.set_defaults(func=self._func_others)
        #Add plugins
        self.plugins = Plugins()
        try:
            core_path = os.path.dirname(os.path.realpath(__file__)) + "/core_plugins"
            self.plugins._import_plugins_to_argparse(core_path, subparsers)
        except:
            pass
        try:
            file_path = self.config.defaultdir + "/plugins"
            self.plugins._import_plugins_to_argparse(file_path, subparsers)
        except:
            pass
        for preload in self.plugins.preloads.values():
            preload.Preload(self)
        #Generate helps
        nodeparser.usage = self._help("usage", subparsers)
        nodeparser.epilog = self._help("end", subparsers)
        nodeparser.help = self._help("node")
        #Manage sys arguments
        self.commands = list(subparsers.choices.keys())
        profilecmds = []
        for action in profileparser._actions:
            profilecmds.extend(action.option_strings)
        if len(argv) >= 2 and argv[1] == "profile" and argv[0] in profilecmds:
            argv[1] = argv[0]
            argv[0] = "profile"
        if len(argv) < 1 or argv[0] not in self.commands:
            argv.insert(0,"node")
        args = defaultparser.parse_args(argv)
        if args.subcommand in self.plugins.plugins:
            self.plugins.plugins[args.subcommand].Entrypoint(args, self.plugins.plugin_parsers[args.subcommand].parser, self)
        else:
            return args.func(args)

    class _store_type(argparse.Action):
        #Custom store type for cli app.
        def __call__(self, parser, args, values, option_string=None):
            setattr(args, "data", values)
            delattr(args,self.dest)
            setattr(args, "command", self.dest)

    def _func_node(self, args):
        #Function called when connecting or managing nodes.
        if not self.case and args.data != None:
            args.data = args.data.lower()
        actions = {"version": self._version, "connect": self._connect, "add": self._add, "del": self._del, "mod": self._mod, "show": self._show}
        return actions.get(args.action)(args)

    def _version(self, args):
        print(__version__)

    def _connect(self, args):
        if args.data == None:
            matches = self.nodes_list
            if len(matches) == 0:
                print("There are no nodes created")
                print("try: conn --help")
                exit(9)
        else:
            if args.data.startswith("@"):
                matches = list(filter(lambda k: args.data in k, self.nodes_list))
            else:
                matches = list(filter(lambda k: k.startswith(args.data), self.nodes_list))
        if len(matches) == 0:
            print("{} not found".format(args.data))
            exit(2)
        elif len(matches) > 1:
            matches[0] = self._choose(matches,"node", "connect")
        if matches[0] == None:
            exit(7)
        node = self.config.getitem(matches[0])
        node = self.node(matches[0],**node, config = self.config)
        if args.sftp:
            node.protocol = "sftp"
        if args.debug:
            node.interact(debug = True)
        else:
            node.interact()

    def _del(self, args):
        if args.data == None:
            print("Missing argument node")
            exit(3)
        elif args.data.startswith("@"):
            matches = list(filter(lambda k: k == args.data, self.folders))
        else:
            matches = self.config._getallnodes(args.data)
        if len(matches) == 0:
            print("{} not found".format(args.data))
            exit(2)
        print("Removing: {}".format(matches))
        question = [inquirer.Confirm("delete", message="Are you sure you want to continue?")]
        confirm = inquirer.prompt(question)
        if confirm == None:
            exit(7)
        if confirm["delete"]:
            if args.data.startswith("@"):
                uniques = self.config._explode_unique(matches[0])
                self.config._folder_del(**uniques)
            else:
                for node in matches:
                    nodeuniques = self.config._explode_unique(node)
                    self.config._connections_del(**nodeuniques)
            self.config._saveconfig(self.config.file)
            if len(matches) == 1:
                print("{} deleted succesfully".format(matches[0]))
            else:
                print(f"{len(matches)} nodes deleted succesfully")

    def _add(self, args):
        args.data = self._type_node(args.data)
        if args.data == None:
            print("Missing argument node")
            exit(3)
        elif args.data.startswith("@"):
            type = "folder"
            matches = list(filter(lambda k: k == args.data, self.folders))
            reversematches = list(filter(lambda k: "@" + k == args.data, self.nodes_list))
        else:
            type = "node"
            matches = list(filter(lambda k: k == args.data, self.nodes_list))
            reversematches = list(filter(lambda k: k == "@" + args.data, self.folders))
        if len(matches) > 0:
            print("{} already exist".format(matches[0]))
            exit(4)
        if len(reversematches) > 0:
            print("{} already exist".format(reversematches[0]))
            exit(4)
        else:
            if type == "folder":
                uniques = self.config._explode_unique(args.data)
                if uniques == False:
                    print("Invalid folder {}".format(args.data))
                    exit(5)
                if "subfolder" in uniques.keys():
                    parent = "@" + uniques["folder"]
                    if parent not in self.folders:
                        print("Folder {} not found".format(uniques["folder"]))
                        exit(2)
                self.config._folder_add(**uniques)
                self.config._saveconfig(self.config.file)
                print("{} added succesfully".format(args.data))
            if type == "node":
                nodefolder = args.data.partition("@")
                nodefolder = "@" + nodefolder[2]
                if nodefolder not in self.folders and nodefolder != "@":
                    print(nodefolder + " not found")
                    exit(2)
                uniques = self.config._explode_unique(args.data)
                if uniques == False:
                    print("Invalid node {}".format(args.data))
                    exit(5)
                self._print_instructions()
                newnode = self._questions_nodes(args.data, uniques)
                if newnode == False:
                    exit(7)
                self.config._connections_add(**newnode)
                self.config._saveconfig(self.config.file)
                print("{} added succesfully".format(args.data))

    def _show(self, args):
        if args.data == None:
            print("Missing argument node")
            exit(3)
        matches = list(filter(lambda k: k == args.data, self.nodes_list))
        if len(matches) == 0:
            print("{} not found".format(args.data))
            exit(2)
        node = self.config.getitem(matches[0])
        for k, v in node.items():
            if isinstance(v, str):
                print(k + ": " + v)
            elif isinstance(v, list):
                print(k + ":")
                for i in v:
                    print("  - " + i)
            elif isinstance(v, dict):
                print(k + ":")
                for i,d in v.items():
                    print("  - " + i + ": " + d)

    def _mod(self, args):
        if args.data == None:
            print("Missing argument node")
            exit(3)
        matches = self.config._getallnodes(args.data)
        if len(matches) == 0:
            print("No connection found with filter: {}".format(args.data))
            exit(2)
        elif len(matches) == 1:
            uniques = self.config._explode_unique(args.data)
            unique = matches[0]
        else:
            uniques = {"id": None, "folder": None}
            unique = None
        print("Editing: {}".format(matches))
        node = {}
        for i in matches:
            node[i] = self.config.getitem(i)
        edits = self._questions_edit()
        if edits == None:
            exit(7)
        updatenode = self._questions_nodes(unique, uniques, edit=edits)
        if not updatenode:
            exit(7)
        if len(matches) == 1:
            uniques.update(node[matches[0]])
            uniques["type"] = "connection"
            if sorted(updatenode.items()) == sorted(uniques.items()):
                print("Nothing to do here")
                return
            else:
                self.config._connections_add(**updatenode)
                self.config._saveconfig(self.config.file)
                print("{} edited succesfully".format(args.data))
        else:
            for k in node:
                updatednode = self.config._explode_unique(k)
                updatednode["type"] = "connection"
                updatednode.update(node[k])
                editcount = 0
                for key, should_edit in edits.items():
                    if should_edit:
                        editcount += 1
                        updatednode[key] = updatenode[key]
                if not editcount:
                    print("Nothing to do here")
                    return
                else:
                    self.config._connections_add(**updatednode)
            self.config._saveconfig(self.config.file)
            print("{} edited succesfully".format(matches))
            return


    def _func_profile(self, args):
        #Function called when managing profiles
        if not self.case:
            args.data[0] = args.data[0].lower()
        actions = {"add": self._profile_add, "del": self._profile_del, "mod": self._profile_mod, "show": self._profile_show}
        return actions.get(args.action)(args)

    def _profile_del(self, args):
        matches = list(filter(lambda k: k == args.data[0], self.profiles))
        if len(matches) == 0:
            print("{} not found".format(args.data[0]))
            exit(2)
        if matches[0] == "default":
            print("Can't delete default profile")
            exit(6)
        usedprofile = self.config._profileused(matches[0])
        if len(usedprofile) > 0:
            print("Profile {} used in the following nodes:".format(matches[0]))
            print(", ".join(usedprofile))
            exit(8)
        question = [inquirer.Confirm("delete", message="Are you sure you want to delete {}?".format(matches[0]))]
        confirm = inquirer.prompt(question)
        if confirm["delete"]:
            self.config._profiles_del(id = matches[0])
            self.config._saveconfig(self.config.file)
            print("{} deleted succesfully".format(matches[0]))

    def _profile_show(self, args):
        matches = list(filter(lambda k: k == args.data[0], self.profiles))
        if len(matches) == 0:
            print("{} not found".format(args.data[0]))
            exit(2)
        profile = self.config.profiles[matches[0]]
        for k, v in profile.items():
            if isinstance(v, str):
                print(k + ": " + v)
            elif isinstance(v, list):
                print(k + ":")
                for i in v:
                    print("  - " + i)
            elif isinstance(v, dict):
                print(k + ":")
                for i,d in v.items():
                    print("  - " + i + ": " + d)

    def _profile_add(self, args):
        matches = list(filter(lambda k: k == args.data[0], self.profiles))
        if len(matches) > 0:
            print("Profile {} Already exist".format(matches[0]))
            exit(4)
        newprofile = self._questions_profiles(args.data[0])
        if newprofile == False:
            exit(7)
        self.config._profiles_add(**newprofile)
        self.config._saveconfig(self.config.file)
        print("{} added succesfully".format(args.data[0]))

    def _profile_mod(self, args):
        matches = list(filter(lambda k: k == args.data[0], self.profiles))
        if len(matches) == 0:
            print("{} not found".format(args.data[0]))
            exit(2)
        profile = self.config.profiles[matches[0]]
        oldprofile = {"id": matches[0]}
        oldprofile.update(profile)
        edits = self._questions_edit()
        if edits == None:
            exit(7)
        updateprofile = self._questions_profiles(matches[0], edit=edits)
        if not updateprofile:
            exit(7)
        if sorted(updateprofile.items()) == sorted(oldprofile.items()):
            print("Nothing to do here")
            return
        else:
            self.config._profiles_add(**updateprofile)
            self.config._saveconfig(self.config.file)
            print("{} edited succesfully".format(args.data[0]))
    
    def _func_others(self, args):
        #Function called when using other commands
        actions = {"ls": self._ls, "move": self._mvcp, "cp": self._mvcp, "bulk": self._bulk, "completion": self._completion, "case": self._case, "fzf": self._fzf, "idletime": self._idletime, "configfolder": self._configfolder, "organization": self._openai, "api_key": self._openai, "model": self._openai}
        return actions.get(args.command)(args)

    def _ls(self, args):
        if args.data == "nodes":
            attribute = "nodes_list"
        else:
            attribute = args.data
        items = getattr(self, attribute)
        if args.filter:
            items = [ item for item in items if re.search(args.filter[0], item)]
        if args.format and args.data == "nodes":
            newitems = []
            for i in items:
                formated = {}
                info = self.config.getitem(i)
                if "@" in i:
                    name_part, location_part = i.split("@", 1)
                    formated["location"] = "@" + location_part
                else:
                    name_part = i
                    formated["location"] = ""
                formated["name"] = name_part
                formated["host"] = info["host"]
                items_copy = list(formated.items())
                for key, value in items_copy:
                    upper_key = key.upper()
                    upper_value = value.upper()
                    formated[upper_key] = upper_value
                newitems.append(args.format[0].format(**formated))
            items = newitems
        print(*items, sep="\n")

    def _mvcp(self, args):
        if not self.case:
            args.data[0] = args.data[0].lower()
            args.data[1] = args.data[1].lower()
        source = list(filter(lambda k: k == args.data[0], self.nodes_list))
        dest = list(filter(lambda k: k == args.data[1], self.nodes_list))
        if len(source) != 1:
            print("{} not found".format(args.data[0]))
            exit(2)
        if len(dest) > 0:
            print("Node {} Already exist".format(args.data[1]))
            exit(4)
        nodefolder = args.data[1].partition("@")
        nodefolder = "@" + nodefolder[2]
        if nodefolder not in self.folders and nodefolder != "@":
            print("{} not found".format(nodefolder))
            exit(2)
        olduniques = self.config._explode_unique(args.data[0])
        newuniques = self.config._explode_unique(args.data[1])
        if newuniques == False:
            print("Invalid node {}".format(args.data[1]))
            exit(5)
        node = self.config.getitem(source[0])
        newnode = {**newuniques, **node}
        self.config._connections_add(**newnode)
        if args.command == "move":
           self.config._connections_del(**olduniques) 
        self.config._saveconfig(self.config.file)
        action = "moved" if args.command == "move" else "copied"
        print("{} {} succesfully to {}".format(args.data[0],action, args.data[1]))

    def _bulk(self, args):
        newnodes = self._questions_bulk()
        if newnodes == False:
            exit(7)
        if not self.case:
            newnodes["location"] = newnodes["location"].lower()
            newnodes["ids"] = newnodes["ids"].lower()
        ids = newnodes["ids"].split(",")
        hosts = newnodes["host"].split(",")
        count = 0
        for n in ids:
            unique = n + newnodes["location"]
            matches = list(filter(lambda k: k == unique, self.nodes_list))
            reversematches = list(filter(lambda k: k == "@" + unique, self.folders))
            if len(matches) > 0:
                print("Node {} already exist, ignoring it".format(unique))
                continue
            if len(reversematches) > 0:
                print("Folder with name {} already exist, ignoring it".format(unique))
                continue
            newnode = {"id": n}
            if newnodes["location"] != "":
                location = self.config._explode_unique(newnodes["location"])
                newnode.update(location)
            if len(hosts) > 1:
                index = ids.index(n)
                newnode["host"] = hosts[index]
            else:
                newnode["host"] = hosts[0]
            newnode["protocol"] = newnodes["protocol"]
            newnode["port"] = newnodes["port"]
            newnode["options"] = newnodes["options"]
            newnode["logs"] = newnodes["logs"]
            newnode["tags"] = newnodes["tags"]
            newnode["jumphost"] = newnodes["jumphost"]
            newnode["user"] = newnodes["user"]
            newnode["password"] = newnodes["password"]
            count +=1
            self.config._connections_add(**newnode)
            self.nodes_list = self.config._getallnodes()
        if count > 0:
            self.config._saveconfig(self.config.file)
            print("Succesfully added {} nodes".format(count))
        else:
            print("0 nodes added")

    def _completion(self, args):
        if args.data[0] == "bash":
            print(self._help("bashcompletion"))
        elif args.data[0] == "zsh":
            print(self._help("zshcompletion"))

    def _case(self, args):
        if args.data[0] == "true":
            args.data[0] = True
        elif args.data[0] == "false":
            args.data[0] = False
        self._change_settings(args.command, args.data[0])

    def _fzf(self, args):
        if args.data[0] == "true":
            args.data[0] = True
        elif args.data[0] == "false":
            args.data[0] = False
        self._change_settings(args.command, args.data[0])

    def _idletime(self, args):
        if args.data[0] < 0:
            args.data[0] = 0
        self._change_settings(args.command, args.data[0])

    def _configfolder(self, args):
        if not os.path.isdir(args.data[0]):
            raise argparse.ArgumentTypeError(f"readable_dir:{args.data[0]} is not a valid path")
        else:
            pathfile = self.config.defaultdir + "/.folder"
            folder = os.path.abspath(args.data[0]).rstrip('/')
            with open(pathfile, "w") as f:
                f.write(str(folder))
            print("Config saved")
        
    def _openai(self, args):
        if "openai" in self.config.config:
            openaikeys = self.config.config["openai"]
        else:
            openaikeys = {}
        openaikeys[args.command] = args.data[0]
        self._change_settings("openai", openaikeys)


    def _change_settings(self, name, value):
        self.config.config[name] = value
        self.config._saveconfig(self.config.file)
        print("Config saved")

    def _func_plugin(self, args):
        if args.add:
            if not os.path.exists(args.add[1]):
                print("File {} dosn't exists.".format(args.add[1]))
                exit(14)
            if args.add[0].isalpha() and args.add[0].islower() and len(args.add[0]) <= 15:
                disabled_dest_file = os.path.join(self.config.defaultdir + "/plugins", args.add[0] + ".py.bkp")
                if args.add[0] in self.commands or os.path.exists(disabled_dest_file):
                    print("Plugin name can't be the same as other commands.")
                    exit(15)
                else:
                    check_bad_script = self.plugins.verify_script(args.add[1])
                    if check_bad_script:
                        print(check_bad_script)
                        exit(16)
                    else:
                        try:
                            dest_file = os.path.join(self.config.defaultdir + "/plugins", args.add[0] + ".py")
                            shutil.copy2(args.add[1], dest_file)
                            print(f"Plugin {args.add[0]} added succesfully.")
                        except Exception as e:
                            print(f"Failed importing plugin file. {e}")
                            exit(17)
            else:
                print("Plugin name should be lowercase letters up to 15 characters.")
                exit(15)
        elif args.update:
            if not os.path.exists(args.update[1]):
                print("File {} dosn't exists.".format(args.update[1]))
                exit(14)
            plugin_file = os.path.join(self.config.defaultdir + "/plugins", args.update[0] + ".py")
            disabled_plugin_file = os.path.join(self.config.defaultdir + "/plugins", args.update[0] + ".py.bkp")
            plugin_exist = os.path.exists(plugin_file)
            disabled_plugin_exist = os.path.exists(disabled_plugin_file)
            if plugin_exist or disabled_plugin_exist:
                check_bad_script = self.plugins.verify_script(args.update[1])
                if check_bad_script:
                    print(check_bad_script)
                    exit(16)
                else:
                    try:
                        disabled_dest_file = os.path.join(self.config.defaultdir + "/plugins", args.update[0] + ".py.bkp")
                        dest_file = os.path.join(self.config.defaultdir + "/plugins", args.update[0] + ".py")
                        if disabled_plugin_exist:
                            shutil.copy2(args.update[1], disabled_dest_file)
                        else:
                            shutil.copy2(args.update[1], dest_file)
                        print(f"Plugin {args.update[0]} updated succesfully.")
                    except Exception as e:
                        print(f"Failed updating plugin file. {e}")
                        exit(17)

            else:
                print("Plugin {} dosn't exist.".format(args.update[0]))
                exit(14)
        elif args.delete:
            plugin_file = os.path.join(self.config.defaultdir + "/plugins", args.delete[0] + ".py")
            disabled_plugin_file = os.path.join(self.config.defaultdir + "/plugins", args.delete[0] + ".py.bkp")
            plugin_exist = os.path.exists(plugin_file)
            disabled_plugin_exist = os.path.exists(disabled_plugin_file)
            if not plugin_exist and not disabled_plugin_exist:
                print("Plugin {} dosn't exist.".format(args.delete[0]))
                exit(14)
            question = [inquirer.Confirm("delete", message="Are you sure you want to delete {} plugin?".format(args.delete[0]))]
            confirm = inquirer.prompt(question)
            if confirm == None:
                exit(7)
            if confirm["delete"]:
                try:
                    if plugin_exist:
                        os.remove(plugin_file)
                    elif disabled_plugin_exist:
                        os.remove(disabled_plugin_file)
                    print(f"plugin {args.delete[0]} deleted succesfully.")
                except Exception as e:
                    print(f"Failed deleting plugin file. {e}")
                    exit(17)
        elif args.disable:
            plugin_file = os.path.join(self.config.defaultdir + "/plugins", args.disable[0] + ".py")
            disabled_plugin_file = os.path.join(self.config.defaultdir + "/plugins", args.disable[0] + ".py.bkp")
            if not os.path.exists(plugin_file) or os.path.exists(disabled_plugin_file):
                print("Plugin {} dosn't exist or it's disabled.".format(args.disable[0]))
                exit(14)
            try:
                os.rename(plugin_file, disabled_plugin_file)
                print(f"plugin {args.disable[0]} disabled succesfully.")
            except Exception as e:
                print(f"Failed disabling plugin file. {e}")
                exit(17)
        elif args.enable:
            plugin_file = os.path.join(self.config.defaultdir + "/plugins", args.enable[0] + ".py")
            disabled_plugin_file = os.path.join(self.config.defaultdir + "/plugins", args.enable[0] + ".py.bkp")
            if os.path.exists(plugin_file) or not os.path.exists(disabled_plugin_file):
                print("Plugin {} dosn't exist or it's enabled.".format(args.enable[0]))
                exit(14)
            try:
                os.rename(disabled_plugin_file, plugin_file)
                print(f"plugin {args.enable[0]} enabled succesfully.")
            except Exception as e:
                print(f"Failed enabling plugin file. {e}")
                exit(17)
        elif args.list:
            enabled_files = []
            disabled_files = []
            plugins = {}
        
            # Iterate over all files in the specified folder
            for file in os.listdir(self.config.defaultdir + "/plugins"):
                # Check if the file is a Python file
                if file.endswith('.py'):
                    enabled_files.append(os.path.splitext(file)[0])
                # Check if the file is a Python backup file
                elif file.endswith('.py.bkp'):
                    disabled_files.append(os.path.splitext(os.path.splitext(file)[0])[0])
            if enabled_files:
                plugins["Enabled"] = enabled_files
            if disabled_files:
                plugins["Disabled"] = disabled_files
            if plugins:
                print(yaml.dump(plugins, sort_keys=False))
            else:
                print("There are no plugins added.")




    def _func_import(self, args):
        if not os.path.exists(args.data[0]):
            print("File {} dosn't exist".format(args.data[0]))
            exit(14)
        print("This could overwrite your current configuration!")
        question = [inquirer.Confirm("import", message="Are you sure you want to import {} file?".format(args.data[0]))]
        confirm = inquirer.prompt(question)
        if confirm == None:
            exit(7)
        if confirm["import"]:
            try:
                with open(args.data[0]) as file:
                    imported = yaml.load(file, Loader=yaml.FullLoader)
            except:
                print("failed reading file {}".format(args.data[0]))
                exit(10)
            for k,v in imported.items():
                uniques = self.config._explode_unique(k)
                if "folder" in uniques:
                    folder = f"@{uniques['folder']}"
                    matches = list(filter(lambda k: k == folder, self.folders))
                    if len(matches) == 0:
                        uniquefolder = self.config._explode_unique(folder)
                        self.config._folder_add(**uniquefolder)
                if "subfolder" in uniques:
                    subfolder = f"@{uniques['subfolder']}@{uniques['folder']}"
                    matches = list(filter(lambda k: k == subfolder, self.folders))
                    if len(matches) == 0:
                        uniquesubfolder = self.config._explode_unique(subfolder)
                        self.config._folder_add(**uniquesubfolder)
                uniques.update(v)
                self.config._connections_add(**uniques)
            self.config._saveconfig(self.config.file)
            print("File {} imported succesfully".format(args.data[0]))
        return

    def _func_export(self, args):
        if os.path.exists(args.data[0]):
            print("File {} already exists".format(args.data[0]))
            exit(14)
        if len(args.data[1:]) == 0:
            foldercons = self.config._getallnodesfull(extract = False)
        else:
            for folder in args.data[1:]:
                matches = list(filter(lambda k: k == folder, self.folders))
                if len(matches) == 0 and folder != "@":
                    print("{} folder not found".format(folder))
                    exit(2)
            foldercons = self.config._getallnodesfull(args.data[1:], extract = False)
        with open(args.data[0], "w") as file:
            yaml.dump(foldercons, file, Dumper=NoAliasDumper, default_flow_style=False)
            file.close()
        print("File {} generated succesfully".format(args.data[0]))
        exit()
        return

    def _func_run(self, args):
        if len(args.data) > 1:
            args.action = "noderun"
        actions = {"noderun": self._node_run, "generate": self._yaml_generate, "run": self._yaml_run}
        return actions.get(args.action)(args)

    def _func_ai(self, args):
        arguments = {}
        if args.model:
            arguments["model"] = args.model[0]
        if args.org:
            arguments["org"] = args.org[0]
        if args.api_key:
            arguments["api_key"] = args.api_key[0]
        self.myai = self.ai(self.config, **arguments)
        if args.ask:
            input = " ".join(args.ask)
            request = self.myai.ask(input, dryrun = True)
            if not request["app_related"]:
                mdprint(Markdown(request["response"]))
                print("\r")
            else:
                if request["action"] == "list_nodes":
                    if request["filter"]:
                        nodes = self.config._getallnodes(request["filter"])
                    else:
                        nodes = self.config._getallnodes()
                    list = "\n".join(nodes)
                    print(list)
                else:
                    yaml_data = yaml.dump(request["task"])
                    confirmation = f"I'm going to run the following task:\n```{yaml_data}```"
                    mdprint(Markdown(confirmation))
                    question = [inquirer.Confirm("task", message="Are you sure you want to continue?")]
                    print("\r")
                    confirm = inquirer.prompt(question)
                    if confirm == None:
                        exit(7)
                    if confirm["task"]:
                        script = {}
                        script["name"] = "RESULT"
                        script["output"] = "stdout"
                        script["nodes"] = request["nodes"]
                        script["action"] = request["action"]
                        if "expected" in request:
                            script["expected"] = request["expected"]
                        script.update(request["args"])
                        self._cli_run(script)
        else:
            history = None
            mdprint(Markdown("**Chatbot**: Hi! How can I help you today?\n\n---"))
            while True:
                questions = [
                        inquirer.Text('message', message="User", validate=self._ai_validation),
                    ]
                answers = inquirer.prompt(questions)
                if answers == None:
                    exit(7)
                response, history = self._process_input(answers["message"], history)
                mdprint(Markdown(f"""**Chatbot**:\n{response}\n\n---"""))
        return


    def _ai_validation(self, answers, current, regex = "^.+$"):
        #Validate ai user chat.
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Can't send empty messages")
        return True

    def _process_input(self, input, history):
        response = self.myai.ask(input , chat_history = history, dryrun = True)
        if not response["app_related"]:
            try:
                if not history:
                    history = []
                history.extend(response["chat_history"])
            except:
                if not history:
                    history = None
            return response["response"], history
        else:
            history = None
            if response["action"] == "list_nodes":
                if response["filter"]:
                    nodes = self.config._getallnodes(response["filter"])
                else:
                    nodes = self.config._getallnodes()
                list = "\n".join(nodes)
                response = f"```{list}\n```"
            else:
                yaml_data = yaml.dump(response["task"])
                confirmresponse = f"I'm going to run the following task:\n```{yaml_data}```\nPlease confirm"
                while True:
                    mdprint(Markdown(f"""**Chatbot**:\n{confirmresponse}"""))
                    questions = [
                            inquirer.Text('message', message="User", validate=self._ai_validation),
                        ]
                    answers = inquirer.prompt(questions)
                    if answers == None:
                        exit(7)
                    confirmation = self.myai.confirm(answers["message"])
                    if isinstance(confirmation, bool):
                        if not confirmation:
                            response = "Request cancelled"
                        else:
                            nodes = self.nodes(self.config.getitems(response["nodes"]), config = self.config)
                            if response["action"] == "run":
                                output = nodes.run(**response["args"])
                                response = ""
                            elif response["action"] == "test":
                                result = nodes.test(**response["args"])
                                yaml_result = yaml.dump(result,default_flow_style=False, indent=4)
                                output = nodes.output
                                response = f"This is the result for your test:\n```\n{yaml_result}\n```"
                            for k,v in output.items():
                                response += f"\n***{k}***:\n```\n{v}\n```\n"
                        break
            return response, history

    def _func_api(self, args):
        if args.command == "stop" or args.command == "restart":
            args.data = self.stop_api()
        if args.command == "start" or args.command == "restart":
            if args.data:
                self.start_api(args.data)
            else:
                self.start_api()
        if args.command == "debug":
            if args.data:
                self.debug_api(args.data)
            else:
                self.debug_api()
        return

    def _node_run(self, args):
        command = " ".join(args.data[1:])
        script = {}
        script["name"] = "Output"
        script["action"] = "run"
        script["nodes"] = args.data[0]
        script["commands"] = [command]
        script["output"] = "stdout"
        self._cli_run(script)

    def _yaml_generate(self, args):
        if os.path.exists(args.data[0]):
            print("File {} already exists".format(args.data[0]))
            exit(14)
        else:
            with open(args.data[0], "w") as file:
                file.write(self._help("generate"))
                file.close()
            print("File {} generated succesfully".format(args.data[0]))
            exit()

    def _yaml_run(self, args):
        try:
            with open(args.data[0]) as file:
                scripts = yaml.load(file, Loader=yaml.FullLoader)
        except:
            print("failed reading file {}".format(args.data[0]))
            exit(10)
        for script in scripts["tasks"]:
            self._cli_run(script)


    def _cli_run(self, script):
        args = {}
        try:
            action = script["action"]
            nodelist = script["nodes"]
            args["commands"] = script["commands"]
            output = script["output"]
            if action == "test":
                args["expected"] = script["expected"]
        except KeyError as e:
            print("'{}' is mandatory".format(e.args[0]))
            exit(11)
        nodes = self.config._getallnodes(nodelist)
        if len(nodes) == 0:
            print("{} don't match any node".format(nodelist))
            exit(2)
        nodes = self.nodes(self.config.getitems(nodes), config = self.config)
        stdout = False
        if output is None:
            pass
        elif output == "stdout":
            stdout = True
        elif isinstance(output, str) and action == "run":
            args["folder"] = output
        if "variables" in script:
            args["vars"] = script["variables"]
        if "vars" in script:
            args["vars"] = script["vars"]
        try:
            options = script["options"]
            thisoptions = {k: v for k, v in options.items() if k in ["prompt", "parallel", "timeout"]}
            args.update(thisoptions)
        except:
            options = None
        try:
            size = str(os.get_terminal_size())
            p = re.search(r'.*columns=([0-9]+)', size)
            columns = int(p.group(1))
        except:
            columns = 80
        if action == "run":
            nodes.run(**args)
            print(script["name"].upper() + "-" * (columns - len(script["name"])))
            for i in nodes.status.keys():
                print("   " + i + " " + "-" * (columns - len(i) - 13) + (" PASS(0)" if nodes.status[i] == 0 else " FAIL({})".format(nodes.status[i])))
                if stdout:
                    for line in nodes.output[i].splitlines():
                        print("      " + line)
        elif action == "test":
            nodes.test(**args)
            print(script["name"].upper() + "-" * (columns - len(script["name"])))
            for i in nodes.status.keys():
                print("   " + i + " " + "-" * (columns - len(i) - 13) + (" PASS(0)" if nodes.status[i] == 0 else " FAIL({})".format(nodes.status[i])))
                if nodes.status[i] == 0:
                    max_length = max(len(s) for s in nodes.result[i].keys())
                    for k,v in nodes.result[i].items():
                        print("     TEST for '{}'".format(k) +  " "*(max_length - len(k) + 1) + "--> " + str(v).upper())
                if stdout:
                    if nodes.status[i] == 0:
                        print("     " + "-" * (max_length + 21))
                    for line in nodes.output[i].splitlines():
                        print("      " + line)
        else:
            print("Wrong action '{}'".format(action))
            exit(13)

    def _choose(self, list, name, action):
        #Generates an inquirer list to pick
        if FzfPrompt and self.fzf:
            fzf = FzfPrompt(executable_path="fzf-tmux")
            if not self.case:
                fzf = FzfPrompt(executable_path="fzf-tmux -i")
            answer = fzf.prompt(list, fzf_options="-d 25%")
            if len(answer) == 0:
                return
            else:
                return answer[0]
        else:
            questions = [inquirer.List(name, message="Pick {} to {}:".format(name,action), choices=list, carousel=True)]
            answer = inquirer.prompt(questions)
            if answer == None:
                return
            else:
                return answer[name]

    def _host_validation(self, answers, current, regex = "^.+$"):
        #Validate hostname in inquirer when managing nodes
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Host cannot be empty")
        if current.startswith("@"):
            if current[1:] not in self.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        return True

    def _profile_protocol_validation(self, answers, current, regex = "(^ssh$|^telnet$|^kubectl$|^docker$|^$)"):
        #Validate protocol in inquirer when managing profiles
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Pick between ssh, telnet, kubectl, docker or leave empty")
        return True

    def _protocol_validation(self, answers, current, regex = "(^ssh$|^telnet$|^kubectl$|^docker$|^$|^@.+$)"):
        #Validate protocol in inquirer when managing nodes
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Pick between ssh, telnet, kubectl, docker leave empty or @profile")
        if current.startswith("@"):
            if current[1:] not in self.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        return True

    def _profile_port_validation(self, answers, current, regex = "(^[0-9]*$)"):
        #Validate port in inquirer when managing profiles
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Pick a port between 1-65535, @profile o leave empty")
        try:
            port = int(current)
        except:
            port = 0
        if current != "" and not 1 <= int(port) <= 65535:
            raise inquirer.errors.ValidationError("", reason="Pick a port between 1-65535 or leave empty")
        return True

    def _port_validation(self, answers, current, regex = "(^[0-9]*$|^@.+$)"):
        #Validate port in inquirer when managing nodes
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Pick a port between 1-6553/app5, @profile or leave empty")
        try:
            port = int(current)
        except:
            port = 0
        if current.startswith("@"):
            if current[1:] not in self.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        elif current != "" and not 1 <= int(port) <= 65535:
            raise inquirer.errors.ValidationError("", reason="Pick a port between 1-65535, @profile o leave empty")
        return True

    def _pass_validation(self, answers, current, regex = "(^@.+$)"):
        #Validate password in inquirer
        profiles = current.split(",")
        for i in profiles:
            if not re.match(regex, i) or i[1:] not in self.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(i))
        return True

    def _tags_validation(self, answers, current):
        #Validation for Tags in inquirer when managing nodes
        if current.startswith("@"):
            if current[1:] not in self.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        elif current != "":
            isdict = False
            try:
                isdict = ast.literal_eval(current)
            except:
                pass
            if not isinstance (isdict, dict):
                raise inquirer.errors.ValidationError("", reason="Tags should be a python dictionary.".format(current))
        return True

    def _profile_tags_validation(self, answers, current):
        #Validation for Tags in inquirer when managing profiles
        if current != "":
            isdict = False
            try:
                isdict = ast.literal_eval(current)
            except:
                pass
            if not isinstance (isdict, dict):
                raise inquirer.errors.ValidationError("", reason="Tags should be a python dictionary.".format(current))
        return True

    def _jumphost_validation(self, answers, current):
        #Validation for Jumphost in inquirer when managing nodes
        if current.startswith("@"):
            if current[1:] not in self.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        elif current != "":
            if current not in self.nodes_list :
                raise inquirer.errors.ValidationError("", reason="Node {} don't exist.".format(current))
        return True

    def _profile_jumphost_validation(self, answers, current):
        #Validation for Jumphost in inquirer when managing profiles
        if current != "":
            if current not in self.nodes_list :
                raise inquirer.errors.ValidationError("", reason="Node {} don't exist.".format(current))
        return True

    def _default_validation(self, answers, current):
        #Default validation type used in multiples questions in inquirer
        if current.startswith("@"):
            if current[1:] not in self.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        return True

    def _bulk_node_validation(self, answers, current, regex = "^[0-9a-zA-Z_.,$#-]+$"):
        #Validation of nodes when running bulk command
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Host cannot be empty")
        if current.startswith("@"):
            if current[1:] not in self.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        return True

    def _bulk_folder_validation(self, answers, current):
        #Validation of folders when running bulk command
        if not self.case:
            current = current.lower()
        matches = list(filter(lambda k: k == current, self.folders))
        if current != "" and len(matches) == 0:
            raise inquirer.errors.ValidationError("", reason="Location {} don't exist".format(current))
        return True

    def _bulk_host_validation(self, answers, current, regex = "^.+$"):
        #Validate hostname when running bulk command
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Host cannot be empty")
        if current.startswith("@"):
            if current[1:] not in self.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        hosts = current.split(",")
        nodes = answers["ids"].split(",")
        if len(hosts) > 1 and len(hosts) != len(nodes):
                raise inquirer.errors.ValidationError("", reason="Hosts list should be the same length of nodes list")
        return True

    def _questions_edit(self):
        #Inquirer questions when editing nodes or profiles
        questions = []
        questions.append(inquirer.Confirm("host", message="Edit Hostname/IP?"))
        questions.append(inquirer.Confirm("protocol", message="Edit Protocol/app?"))
        questions.append(inquirer.Confirm("port", message="Edit Port?"))
        questions.append(inquirer.Confirm("options", message="Edit Options?"))
        questions.append(inquirer.Confirm("logs", message="Edit logging path/file?"))
        questions.append(inquirer.Confirm("tags", message="Edit tags?"))
        questions.append(inquirer.Confirm("jumphost", message="Edit jumphost?"))
        questions.append(inquirer.Confirm("user", message="Edit User?"))
        questions.append(inquirer.Confirm("password", message="Edit password?"))
        answers = inquirer.prompt(questions)
        return answers

    def _questions_nodes(self, unique, uniques = None, edit = None):
        #Questions when adding or editing nodes
        try:
            defaults = self.config.getitem(unique)
            if "tags" not in defaults:
                defaults["tags"] = ""
            if "jumphost" not in defaults:
                defaults["jumphost"] = ""
        except:
            defaults = { "host":"", "protocol":"", "port":"", "user":"", "options":"", "logs":"" , "tags":"", "password":"", "jumphost":""}
        node = {}
        if edit == None:
            edit = { "host":True, "protocol":True, "port":True, "user":True, "password": True,"options":True, "logs":True, "tags":True, "jumphost":True }
        questions = []
        if edit["host"]:
            questions.append(inquirer.Text("host", message="Add Hostname or IP", validate=self._host_validation, default=defaults["host"]))
        else:
            node["host"] = defaults["host"]
        if edit["protocol"]:
            questions.append(inquirer.Text("protocol", message="Select Protocol/app", validate=self._protocol_validation, default=defaults["protocol"]))
        else:
            node["protocol"] = defaults["protocol"]
        if edit["port"]:
            questions.append(inquirer.Text("port", message="Select Port Number", validate=self._port_validation, default=defaults["port"]))
        else:
            node["port"] = defaults["port"]
        if edit["options"]:
            questions.append(inquirer.Text("options", message="Pass extra options to protocol/app", validate=self._default_validation, default=defaults["options"]))
        else:
            node["options"] = defaults["options"]
        if edit["logs"]:
            questions.append(inquirer.Text("logs", message="Pick logging path/file ",  validate=self._default_validation, default=defaults["logs"].replace("{","{{").replace("}","}}")))
        else:
            node["logs"] = defaults["logs"]
        if edit["tags"]:
            questions.append(inquirer.Text("tags", message="Add tags dictionary",  validate=self._tags_validation, default=str(defaults["tags"]).replace("{","{{").replace("}","}}")))
        else:
            node["tags"] = defaults["tags"]
        if edit["jumphost"]:
            questions.append(inquirer.Text("jumphost", message="Add Jumphost node",  validate=self._jumphost_validation, default=str(defaults["jumphost"]).replace("{","{{").replace("}","}}")))
        else:
            node["jumphost"] = defaults["jumphost"]
        if edit["user"]:
            questions.append(inquirer.Text("user", message="Pick username", validate=self._default_validation, default=defaults["user"]))
        else:
            node["user"] = defaults["user"]
        if edit["password"]:
            questions.append(inquirer.List("password", message="Password: Use a local password, no password or a list of profiles to reference?", choices=["Local Password", "Profiles", "No Password"]))
        else:
            node["password"] = defaults["password"]
        answer = inquirer.prompt(questions)
        if answer == None:
            return False
        if "password" in answer.keys():
            if answer["password"] == "Local Password":
                passq = [inquirer.Password("password", message="Set Password")]
                passa = inquirer.prompt(passq)
                if passa == None:
                    return False
                answer["password"] = self.config.encrypt(passa["password"])
            elif answer["password"] == "Profiles":
                passq = [(inquirer.Text("password", message="Set a @profile or a comma separated list of @profiles", validate=self._pass_validation))]
                passa = inquirer.prompt(passq)
                if passa == None:
                    return False
                answer["password"] = passa["password"].split(",")
            elif answer["password"] == "No Password":
                answer["password"] = ""
        if "tags" in answer.keys() and not answer["tags"].startswith("@") and answer["tags"]:
            answer["tags"] = ast.literal_eval(answer["tags"])
        result = {**uniques, **answer, **node}
        result["type"] = "connection"
        return result

    def _questions_profiles(self, unique, edit = None):
        #Questions when adding or editing profiles
        try:
            defaults = self.config.profiles[unique]
            if "tags" not in defaults:
                defaults["tags"] = ""
            if "jumphost" not in defaults:
                defaults["jumphost"] = ""
        except:
            defaults = { "host":"", "protocol":"", "port":"", "user":"", "options":"", "logs":"", "tags": "", "jumphost": ""}
        profile = {}
        if edit == None:
            edit = { "host":True, "protocol":True, "port":True, "user":True, "password": True,"options":True, "logs":True, "tags":True, "jumphost":True }
        questions = []
        if edit["host"]:
            questions.append(inquirer.Text("host", message="Add Hostname or IP", default=defaults["host"]))
        else:
            profile["host"] = defaults["host"]
        if edit["protocol"]:
            questions.append(inquirer.Text("protocol", message="Select Protocol/app", validate=self._profile_protocol_validation, default=defaults["protocol"]))
        else:
            profile["protocol"] = defaults["protocol"]
        if edit["port"]:
            questions.append(inquirer.Text("port", message="Select Port Number", validate=self._profile_port_validation, default=defaults["port"]))
        else:
            profile["port"] = defaults["port"]
        if edit["options"]:
            questions.append(inquirer.Text("options", message="Pass extra options to protocol/app", default=defaults["options"]))
        else:
            profile["options"] = defaults["options"]
        if edit["logs"]:
            questions.append(inquirer.Text("logs", message="Pick logging path/file ", default=defaults["logs"].replace("{","{{").replace("}","}}")))
        else:
            profile["logs"] = defaults["logs"]
        if edit["tags"]:
            questions.append(inquirer.Text("tags", message="Add tags dictionary",  validate=self._profile_tags_validation, default=str(defaults["tags"]).replace("{","{{").replace("}","}}")))
        else:
            profile["tags"] = defaults["tags"]
        if edit["jumphost"]:
            questions.append(inquirer.Text("jumphost", message="Add Jumphost node",  validate=self._profile_jumphost_validation, default=str(defaults["jumphost"]).replace("{","{{").replace("}","}}")))
        else:
            profile["jumphost"] = defaults["jumphost"]
        if edit["user"]:
            questions.append(inquirer.Text("user", message="Pick username", default=defaults["user"]))
        else:
            profile["user"] = defaults["user"]
        if edit["password"]:
            questions.append(inquirer.Password("password", message="Set Password"))
        else:
            profile["password"] = defaults["password"]
        answer = inquirer.prompt(questions)
        if answer == None:
            return False
        if "password" in answer.keys():
            if answer["password"] != "":
                answer["password"] = self.config.encrypt(answer["password"])
        if "tags" in answer.keys() and answer["tags"]:
            answer["tags"] = ast.literal_eval(answer["tags"])
        result = {**answer, **profile}
        result["id"] = unique
        return result

    def _questions_bulk(self):
        #Questions when using bulk command
        questions = []
        questions.append(inquirer.Text("ids", message="add a comma separated list of nodes to add", validate=self._bulk_node_validation))
        questions.append(inquirer.Text("location", message="Add a @folder, @subfolder@folder or leave empty", validate=self._bulk_folder_validation))
        questions.append(inquirer.Text("host", message="Add comma separated list of Hostnames or IPs", validate=self._bulk_host_validation))
        questions.append(inquirer.Text("protocol", message="Select Protocol/app", validate=self._protocol_validation))
        questions.append(inquirer.Text("port", message="Select Port Number", validate=self._port_validation))
        questions.append(inquirer.Text("options", message="Pass extra options to protocol/app", validate=self._default_validation))
        questions.append(inquirer.Text("logs", message="Pick logging path/file ", validate=self._default_validation))
        questions.append(inquirer.Text("tags", message="Add tags dictionary",  validate=self._tags_validation))
        questions.append(inquirer.Text("jumphost", message="Add Jumphost node",  validate=self._jumphost_validation))
        questions.append(inquirer.Text("user", message="Pick username", validate=self._default_validation))
        questions.append(inquirer.List("password", message="Password: Use a local password, no password or a list of profiles to reference?", choices=["Local Password", "Profiles", "No Password"]))
        answer = inquirer.prompt(questions)
        if answer == None:
            return False
        if "password" in answer.keys():
            if answer["password"] == "Local Password":
                passq = [inquirer.Password("password", message="Set Password")]
                passa = inquirer.prompt(passq)
                answer["password"] = self.config.encrypt(passa["password"])
            elif answer["password"] == "Profiles":
                passq = [(inquirer.Text("password", message="Set a @profile or a comma separated list of @profiles", validate=self._pass_validation))]
                passa = inquirer.prompt(passq)
                answer["password"] = passa["password"].split(",")
            elif answer["password"] == "No Password":
                answer["password"] = ""
        answer["type"] = "connection"
        if "tags" in answer.keys() and not answer["tags"].startswith("@") and answer["tags"]:
            answer["tags"] = ast.literal_eval(answer["tags"])
        return answer

    def _type_node(self, arg_value, pat=re.compile(r"^[0-9a-zA-Z_.$@#-]+$")):
        if arg_value == None:
            raise ValueError("Missing argument node")
        if not pat.match(arg_value):
            raise ValueError(f"Argument error: {arg_value}")
        return arg_value
    
    def _type_profile(self, arg_value, pat=re.compile(r"^[0-9a-zA-Z_.$#-]+$")):
        if not pat.match(arg_value):
            raise ValueError
        return arg_value

    def _help(self, type, parsers = None):
        #Store text for help and other commands
        if type == "node":
            return "node[@subfolder][@folder]\nConnect to specific node or show all matching nodes\n[@subfolder][@folder]\nShow all available connections globally or in specified path"
        if type == "usage":
            commands = []
            for subcommand, subparser in parsers.choices.items():
                if subparser.description != None:
                    commands.append(subcommand)
            commands = ",".join(commands)
            usage_help = f"conn [-h] [--add | --del | --mod | --show | --debug] [node|folder] [--sftp]\n       conn {{{commands}}} ..."
            return usage_help
        if type == "end":
            help_dict = {}
            for subcommand, subparser in parsers.choices.items():
                if subparser.description == None and help_dict:
                    previous_key = next(reversed(help_dict.keys()))
                    help_dict[f"{previous_key}({subcommand})"] = help_dict.pop(previous_key)
                else:
                    help_dict[subcommand] = subparser.description
                subparser.description = None
            commands_help = "Commands:\n"
            commands_help += "\n".join([f"  {cmd:<15} {help_text}" for cmd, help_text in help_dict.items() if help_text != None])
            return commands_help
        if type == "bashcompletion":
            return '''
#Here starts bash completion for conn
_conn()
{
        mapfile -t strings < <(connpy-completion-helper "bash" "${#COMP_WORDS[@]}" "${COMP_WORDS[@]}")
        local IFS=$'\t\n'
        local home_dir=$(eval echo ~)
        local last_word=${COMP_WORDS[-1]/\~/$home_dir}
        COMPREPLY=($(compgen -W "$(printf '%s' "${strings[@]}")" -- "$last_word"))
        if [ "$last_word" != "${COMP_WORDS[-1]}" ]; then
            COMPREPLY=(${COMPREPLY[@]/$home_dir/\~})
        fi
}

complete -o nospace -o nosort -F _conn conn
complete -o nospace -o nosort -F _conn connpy
#Here ends bash completion for conn
        '''
        if type == "zshcompletion":
            return '''
#Here starts zsh completion for conn
autoload -U compinit && compinit
_conn()
{
    local home_dir=$(eval echo ~)
    last_word=${words[-1]/\~/$home_dir}
    strings=($(connpy-completion-helper "zsh" ${#words} $words[1,-2] $last_word))
    for string in "${strings[@]}"; do
        #Replace the expanded home directory with ~
        if [ "$last_word" != "$words[-1]" ]; then
            string=${string/$home_dir/\~}
        fi
        if [[ "${string}" =~ .*/$ ]]; then
            # If the string ends with a '/', do not append a space
            compadd -Q -S '' -- "$string"
        else
            # If the string does not end with a '/', append a space
            compadd -Q -S ' ' -- "$string"
        fi
    done
}
compdef _conn conn
compdef _conn connpy
#Here ends zsh completion for conn
            '''
        if type == "run":
            return "node[@subfolder][@folder] commmand to run\nRun the specific command on the node and print output\n/path/to/file.yaml\nUse a yaml file to run an automation script"
        if type == "generate":
            return '''---
tasks:
- name: "Config"

  action: 'run' #Action can be test or run. Mandatory

  nodes: #List of nodes to work on. Mandatory
  - 'router1@office' #You can add specific nodes
  - '@aws'  #entire folders or subfolders
  - '@office':   #or filter inside a folder or subfolder
    - 'router2'
    - 'router7'

  commands: #List of commands to send, use {name} to pass variables
  - 'term len 0'
  - 'conf t'
  - 'interface {if}'
  - 'ip address 10.100.100.{id} 255.255.255.255'
  - '{commit}'
  - 'end'

  variables: #Variables to use on commands and expected. Optional
    __global__: #Global variables to use on all nodes, fallback if missing in the node.
      commit: ''
      if: 'loopback100'
    router1@office:
      id: 1
    router2@office:
      id: 2
      commit: 'commit'
    router3@office:
      id: 3
    vrouter1@aws:
      id: 4
    vrouterN@aws:
      id: 5
  
  output: /home/user/logs #Type of output, if null you only get Connection and test result. Choices are: null,stdout,/path/to/folder. Folder path only works on 'run' action.
  
  options:
    prompt: r'>$|#$|\$$|>.$|#.$|\$.$' #Optional prompt to check on your devices, default should work on most devices.
    parallel: 10 #Optional number of nodes to run commands on parallel. Default 10.
    timeout: 20 #Optional time to wait in seconds for prompt, expected or EOF. Default 20. 

- name: "TestConfig"
  action: 'test'
  nodes:
  - 'router1@office'
  - '@aws'
  - '@office':
    - 'router2'
    - 'router7'
  commands:
  - 'ping 10.100.100.{id}'
  expected: '!' #Expected text to find when running test action. Mandatory for 'test'
  variables:
    router1@office:
      id: 1
    router2@office:
      id: 2
      commit: 'commit'
    router3@office:
      id: 3
    vrouter1@aws:
      id: 4
    vrouterN@aws:
      id: 5
  output: null
...'''

    def _print_instructions(self):
        instructions = """
Welcome to Connpy node Addition Wizard!

Here are some important instructions and tips for configuring your new node:

1. **Profiles**:
   - You can use the configured settings in a profile using `@profilename`.

2. **Available Protocols and Apps**:
   - ssh
   - telnet
   - kubectl (`kubectl exec`)
   - docker (`docker exec`)

3. **Optional Values**:
   - You can leave any value empty except for the hostname/IP.

4. **Passwords**:
   - You can pass one or more passwords using comma-separated `@profiles`.

5. **Logging**:
   - You can use the following variables in the logging file name:
     - `${id}`
     - `${unique}`
     - `${host}`
     - `${port}`
     - `${user}`
     - `${protocol}`

6. **Well-Known Tags**:
   - `os`: Identified by AI to generate commands based on the operating system.
   - `screen_length_command`: Used by automation to avoid pagination on different devices (e.g., `terminal length 0` for Cisco devices).
   - `prompt`: Replaces default app prompt to identify the end of output or where the user can start inputting commands.
   - `kube_command`: Replaces the default command (`/bin/bash`) for `kubectl exec`.
   - `docker_command`: Replaces the default command for `docker exec`.

Please follow these instructions carefully to ensure proper configuration of your new node.
"""

        # print(instructions)
        mdprint(Markdown(instructions))
