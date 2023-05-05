#!/usr/bin/env python3
#Imports
import os
import re
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
import ast
import argparse
import sys
import inquirer
from .core import node,nodes
from ._version import __version__
from .api import start_api,stop_api,debug_api
import yaml
import ast
try:
    from pyfzf.pyfzf import FzfPrompt
except:
    FzfPrompt = None
home = os.path.expanduser("~")
defaultdir = home + '/.config/conn'



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
        self.node = node
        self.connnodes = nodes
        self.config = config
        self.nodes = self.config._getallnodes()
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
        subparsers = defaultparser.add_subparsers(title="Commands")
        #NODEPARSER
        nodeparser = subparsers.add_parser("node",usage=self._help("usage"), help=self._help("node"),epilog=self._help("end"), formatter_class=argparse.RawTextHelpFormatter) 
        nodecrud = nodeparser.add_mutually_exclusive_group()
        nodeparser.add_argument("node", metavar="node|folder", nargs='?', default=None, action=self._store_type, type=self._type_node, help=self._help("node"))
        nodecrud.add_argument("-v","--version", dest="action", action="store_const", help="Show version", const="version", default="connect")
        nodecrud.add_argument("-a","--add", dest="action", action="store_const", help="Add new node[@subfolder][@folder] or [@subfolder]@folder", const="add", default="connect")
        nodecrud.add_argument("-r","--del", "--rm", dest="action", action="store_const", help="Delete node[@subfolder][@folder] or [@subfolder]@folder", const="del", default="connect")
        nodecrud.add_argument("-e","--mod", "--edit", dest="action", action="store_const", help="Modify node[@subfolder][@folder]", const="mod", default="connect")
        nodecrud.add_argument("-s","--show", dest="action", action="store_const", help="Show node[@subfolder][@folder]", const="show", default="connect")
        nodecrud.add_argument("-d","--debug", dest="action", action="store_const", help="Display all conections steps", const="debug", default="connect")
        nodeparser.set_defaults(func=self._func_node)
        #PROFILEPARSER
        profileparser = subparsers.add_parser("profile", help="Manage profiles") 
        profileparser.add_argument("profile", nargs=1, action=self._store_type, type=self._type_profile, help="Name of profile to manage")
        profilecrud = profileparser.add_mutually_exclusive_group(required=True)
        profilecrud.add_argument("-a", "--add", dest="action", action="store_const", help="Add new profile", const="add")
        profilecrud.add_argument("-r", "--del", "--rm", dest="action", action="store_const", help="Delete profile", const="del")
        profilecrud.add_argument("-e", "--mod", "--edit", dest="action", action="store_const", help="Modify profile", const="mod")
        profilecrud.add_argument("-s", "--show", dest="action", action="store_const", help="Show profile", const="show")
        profileparser.set_defaults(func=self._func_profile)
        #MOVEPARSER
        moveparser = subparsers.add_parser("move", aliases=["mv"], help="Move node") 
        moveparser.add_argument("move", nargs=2, action=self._store_type, help="Move node[@subfolder][@folder] dest_node[@subfolder][@folder]", default="move", type=self._type_node)
        moveparser.set_defaults(func=self._func_others)
        #COPYPARSER
        copyparser = subparsers.add_parser("copy", aliases=["cp"], help="Copy node") 
        copyparser.add_argument("cp", nargs=2, action=self._store_type, help="Copy node[@subfolder][@folder] new_node[@subfolder][@folder]", default="cp", type=self._type_node)
        copyparser.set_defaults(func=self._func_others)
        #LISTPARSER
        lsparser = subparsers.add_parser("list", aliases=["ls"], help="List profiles, nodes or folders") 
        lsparser.add_argument("ls", action=self._store_type, choices=["profiles","nodes","folders"], help="List profiles, nodes or folders", default=False)
        lsparser.set_defaults(func=self._func_others)
        #BULKPARSER
        bulkparser = subparsers.add_parser("bulk", help="Add nodes in bulk") 
        bulkparser.add_argument("bulk", const="bulk", nargs=0, action=self._store_type, help="Add nodes in bulk")
        bulkparser.set_defaults(func=self._func_others)
        #RUNPARSER
        runparser = subparsers.add_parser("run", help="Run scripts or commands on nodes", formatter_class=argparse.RawTextHelpFormatter) 
        runparser.add_argument("run", nargs='+', action=self._store_type, help=self._help("run"), default="run")
        runparser.add_argument("-g","--generate", dest="action", action="store_const", help="Generate yaml file template", const="generate", default="run")
        runparser.set_defaults(func=self._func_run)
        #APIPARSER
        apiparser = subparsers.add_parser("api", help="Start and stop connpy api") 
        apicrud = apiparser.add_mutually_exclusive_group(required=True)
        apicrud.add_argument("-s","--start", dest="start", nargs="?", action=self._store_type, help="Start conppy api", type=int, default=8048, metavar="PORT")
        apicrud.add_argument("-r","--restart", dest="restart", nargs=0, action=self._store_type, help="Restart conppy api")
        apicrud.add_argument("-x","--stop", dest="stop", nargs=0, action=self._store_type, help="Stop conppy api")
        apicrud.add_argument("-d", "--debug", dest="debug", nargs="?", action=self._store_type, help="Run connpy server on debug mode", type=int, default=8048, metavar="PORT")
        apiparser.set_defaults(func=self._func_api)
        #CONFIGPARSER
        configparser = subparsers.add_parser("config", help="Manage app config") 
        configcrud = configparser.add_mutually_exclusive_group(required=True)
        configcrud.add_argument("--allow-uppercase", dest="case", nargs=1, action=self._store_type, help="Allow case sensitive names", choices=["true","false"])
        configcrud.add_argument("--fzf", dest="fzf", nargs=1, action=self._store_type, help="Use fzf for lists", choices=["true","false"])
        configcrud.add_argument("--keepalive", dest="idletime", nargs=1, action=self._store_type, help="Set keepalive time in seconds, 0 to disable", type=int, metavar="INT")
        configcrud.add_argument("--completion", dest="completion", nargs=1, choices=["bash","zsh"], action=self._store_type, help="Get terminal completion configuration for conn")
        configcrud.add_argument("--configfolder", dest="configfolder", nargs=1, action=self._store_type, help="Set the default location for config file", metavar="FOLDER")
        configcrud.add_argument("--openai", dest="openai", nargs=2, action=self._store_type, help="Set openai organization and api_key", metavar=("ORGANIZATION", "API_KEY"))
        configparser.set_defaults(func=self._func_others)
        #Manage sys arguments
        commands = ["node", "profile", "mv", "move","copy", "cp", "bulk", "ls", "list", "run", "config", "api"]
        profilecmds = ["--add", "-a", "--del", "--rm",  "-r", "--mod", "--edit", "-e", "--show", "-s"]
        if len(argv) >= 2 and argv[1] == "profile" and argv[0] in profilecmds:
            argv[1] = argv[0]
            argv[0] = "profile"
        if len(argv) < 1 or argv[0] not in commands:
            argv.insert(0,"node")
        args = defaultparser.parse_args(argv)
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
        actions = {"version": self._version, "connect": self._connect, "debug": self._connect, "add": self._add, "del": self._del, "mod": self._mod, "show": self._show}
        return actions.get(args.action)(args)

    def _version(self, args):
        print(__version__)

    def _connect(self, args):
        if args.data == None:
            matches = self.nodes
            if len(matches) == 0:
                print("There are no nodes created")
                print("try: conn --help")
                exit(9)
        else:
            if args.data.startswith("@"):
                matches = list(filter(lambda k: args.data in k, self.nodes))
            else:
                matches = list(filter(lambda k: k.startswith(args.data), self.nodes))
        if len(matches) == 0:
            print("{} not found".format(args.data))
            exit(2)
        elif len(matches) > 1:
            matches[0] = self._choose(matches,"node", "connect")
        if matches[0] == None:
            exit(7)
        node = self.config.getitem(matches[0])
        node = self.node(matches[0],**node, config = self.config)
        if args.action == "debug":
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
            matches = list(filter(lambda k: k == args.data, self.nodes))
        if len(matches) == 0:
            print("{} not found".format(args.data))
            exit(2)
        question = [inquirer.Confirm("delete", message="Are you sure you want to delete {}?".format(matches[0]))]
        confirm = inquirer.prompt(question)
        if confirm == None:
            exit(7)
        if confirm["delete"]:
            uniques = self.config._explode_unique(matches[0])
            if args.data.startswith("@"):
                self.config._folder_del(**uniques)
            else:
                self.config._connections_del(**uniques)
            self.config._saveconfig(self.config.file)
            print("{} deleted succesfully".format(matches[0]))

    def _add(self, args):
        if args.data == None:
            print("Missing argument node")
            exit(3)
        elif args.data.startswith("@"):
            type = "folder"
            matches = list(filter(lambda k: k == args.data, self.folders))
            reversematches = list(filter(lambda k: "@" + k == args.data, self.nodes))
        else:
            type = "node"
            matches = list(filter(lambda k: k == args.data, self.nodes))
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
                print("You can use the configured setting in a profile using @profilename.")
                print("You can also leave empty any value except hostname/IP.")
                print("You can pass 1 or more passwords using comma separated @profiles")
                print("You can use this variables on logging file name: ${id} ${unique} ${host} ${port} ${user} ${protocol}")
                print("Some useful tags to set for automation are 'os', 'screen_length_command', and 'prompt'.")
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
        matches = list(filter(lambda k: k == args.data, self.nodes))
        if len(matches) == 0:
            print("{} not found".format(args.data))
            exit(2)
        node = self.config.getitem(matches[0])
        for k, v in node.items():
            if isinstance(v, str):
                print(k + ": " + v)
            else:
                print(k + ":")
                for i in v:
                    print("  - " + i)

    def _mod(self, args):
        if args.data == None:
            print("Missing argument node")
            exit(3)
        matches = list(filter(lambda k: k == args.data, self.nodes))
        if len(matches) == 0:
            print("{} not found".format(args.data))
            exit(2)
        node = self.config.getitem(matches[0])
        edits = self._questions_edit()
        if edits == None:
            exit(7)
        uniques = self.config._explode_unique(args.data)
        updatenode = self._questions_nodes(args.data, uniques, edit=edits)
        if not updatenode:
            exit(7)
        uniques.update(node)
        uniques["type"] = "connection"
        if sorted(updatenode.items()) == sorted(uniques.items()):
            print("Nothing to do here")
            return
        else:
            self.config._connections_add(**updatenode)
            self.config._saveconfig(self.config.file)
            print("{} edited succesfully".format(args.data))


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
            else:
                print(k + ":")
                for i in v:
                    print("  - " + i)

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
        actions = {"ls": self._ls, "move": self._mvcp, "cp": self._mvcp, "bulk": self._bulk, "completion": self._completion, "case": self._case, "fzf": self._fzf, "idletime": self._idletime, "configfolder": self._configfolder, "openai": self._openai}
        return actions.get(args.command)(args)

    def _ls(self, args):
        print(*getattr(self, args.data), sep="\n")

    def _mvcp(self, args):
        if not self.case:
            args.data[0] = args.data[0].lower()
            args.data[1] = args.data[1].lower()
        source = list(filter(lambda k: k == args.data[0], self.nodes))
        dest = list(filter(lambda k: k == args.data[1], self.nodes))
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
            matches = list(filter(lambda k: k == unique, self.nodes))
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
            newnode["user"] = newnodes["user"]
            newnode["password"] = newnodes["password"]
            count +=1
            self.config._connections_add(**newnode)
            self.nodes = self.config._getallnodes()
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
            pathfile = defaultdir + "/.folder"
            folder = os.path.abspath(args.data[0]).rstrip('/')
            with open(pathfile, "w") as f:
                f.write(str(folder))
            print("Config saved")
        
    def _openai(self, args):
        openaikeys = {}
        openaikeys["organization"] = args.data[0]
        openaikeys["api_key"] = args.data[1]
        self._change_settings(args.command, openaikeys)


    def _change_settings(self, name, value):
        self.config.config[name] = value
        self.config._saveconfig(self.config.file)
        print("Config saved")

    def _func_run(self, args):
        if len(args.data) > 1:
            args.action = "noderun"
        actions = {"noderun": self._node_run, "generate": self._yaml_generate, "run": self._yaml_run}
        return actions.get(args.action)(args)

    def _func_api(self, args):
        if args.command == "stop" or args.command == "restart":
            args.data = stop_api()
        if args.command == "start" or args.command == "restart":
            if args.data:
                start_api(args.data)
            else:
                start_api()
        if args.command == "debug":
            if args.data:
                debug_api(args.data)
            else:
                debug_api()
        return

    def _node_run(self, args):
        command = " ".join(args.data[1:])
        matches = list(filter(lambda k: k == args.data[0], self.nodes))
        if len(matches) == 0:
            print("{} not found".format(args.data[0]))
            exit(2)
        node = self.config.getitem(matches[0])
        node = self.node(matches[0],**node, config = self.config)
        node.run(command)
        print(node.output)

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
            nodes = self.connnodes(self.config.getitems(nodelist), config = self.config)
            stdout = False
            if output is None:
                pass
            elif output == "stdout":
                stdout = True
            elif isinstance(output, str) and action == "run":
                args["folder"] = output
            try:
                args["vars"] = script["variables"]
            except:
                pass
            try:
                options = script["options"]
                thisoptions = {k: v for k, v in options.items() if k in ["prompt", "parallel", "timeout"]}
                args.update(thisoptions)
            except:
                options = None
            size = str(os.get_terminal_size())
            p = re.search(r'.*columns=([0-9]+)', size)
            columns = int(p.group(1))
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
                        try:
                            myexpected = args["expected"].format(**args["vars"][i])
                        except:
                            try:
                                myexpected = args["expected"].format(**args["vars"]["__global__"])
                            except:
                                myexpected = args["expected"]
                        print("     TEST for '{}' --> ".format(myexpected) + str(nodes.result[i]).upper())
                    if stdout:
                        if nodes.status[i] == 0:
                            print("     " + "-" * (len(myexpected) + 16 + len(str(nodes.result[i]))))
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

    def _profile_protocol_validation(self, answers, current, regex = "(^ssh$|^telnet$|^$)"):
        #Validate protocol in inquirer when managing profiles
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Pick between ssh, telnet or leave empty")
        return True

    def _protocol_validation(self, answers, current, regex = "(^ssh$|^telnet$|^$|^@.+$)"):
        #Validate protocol in inquirer when managing nodes
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Pick between ssh, telnet, leave empty or @profile")
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
            raise inquirer.errors.ValidationError("", reason="Pick a port between 1-65535, @profile or leave empty")
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
        questions.append(inquirer.Confirm("protocol", message="Edit Protocol?"))
        questions.append(inquirer.Confirm("port", message="Edit Port?"))
        questions.append(inquirer.Confirm("options", message="Edit Options?"))
        questions.append(inquirer.Confirm("logs", message="Edit logging path/file?"))
        questions.append(inquirer.Confirm("tags", message="Edit tags?"))
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
        except:
            defaults = { "host":"", "protocol":"", "port":"", "user":"", "options":"", "logs":"" , "tags":""}
        node = {}
        if edit == None:
            edit = { "host":True, "protocol":True, "port":True, "user":True, "password": True,"options":True, "logs":True, "tags":True }
        questions = []
        if edit["host"]:
            questions.append(inquirer.Text("host", message="Add Hostname or IP", validate=self._host_validation, default=defaults["host"]))
        else:
            node["host"] = defaults["host"]
        if edit["protocol"]:
            questions.append(inquirer.Text("protocol", message="Select Protocol", validate=self._protocol_validation, default=defaults["protocol"]))
        else:
            node["protocol"] = defaults["protocol"]
        if edit["port"]:
            questions.append(inquirer.Text("port", message="Select Port Number", validate=self._port_validation, default=defaults["port"]))
        else:
            node["port"] = defaults["port"]
        if edit["options"]:
            questions.append(inquirer.Text("options", message="Pass extra options to protocol", validate=self._default_validation, default=defaults["options"]))
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
                answer["password"] = self.encrypt(passa["password"])
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
        except:
            defaults = { "host":"", "protocol":"", "port":"", "user":"", "options":"", "logs":"", "tags": "" }
        profile = {}
        if edit == None:
            edit = { "host":True, "protocol":True, "port":True, "user":True, "password": True,"options":True, "logs":True, "tags":True }
        questions = []
        if edit["host"]:
            questions.append(inquirer.Text("host", message="Add Hostname or IP", default=defaults["host"]))
        else:
            profile["host"] = defaults["host"]
        if edit["protocol"]:
            questions.append(inquirer.Text("protocol", message="Select Protocol", validate=self._profile_protocol_validation, default=defaults["protocol"]))
        else:
            profile["protocol"] = defaults["protocol"]
        if edit["port"]:
            questions.append(inquirer.Text("port", message="Select Port Number", validate=self._profile_port_validation, default=defaults["port"]))
        else:
            profile["port"] = defaults["port"]
        if edit["options"]:
            questions.append(inquirer.Text("options", message="Pass extra options to protocol", default=defaults["options"]))
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
                answer["password"] = self.encrypt(answer["password"])
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
        questions.append(inquirer.Text("protocol", message="Select Protocol", validate=self._protocol_validation))
        questions.append(inquirer.Text("port", message="Select Port Number", validate=self._port_validation))
        questions.append(inquirer.Text("options", message="Pass extra options to protocol", validate=self._default_validation))
        questions.append(inquirer.Text("logs", message="Pick logging path/file ", validate=self._default_validation))
        questions.append(inquirer.Text("tags", message="Add tags dictionary",  validate=self._tags_validation))
        questions.append(inquirer.Text("user", message="Pick username", validate=self._default_validation))
        questions.append(inquirer.List("password", message="Password: Use a local password, no password or a list of profiles to reference?", choices=["Local Password", "Profiles", "No Password"]))
        answer = inquirer.prompt(questions)
        if answer == None:
            return False
        if "password" in answer.keys():
            if answer["password"] == "Local Password":
                passq = [inquirer.Password("password", message="Set Password")]
                passa = inquirer.prompt(passq)
                answer["password"] = self.encrypt(passa["password"])
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
        if not pat.match(arg_value):
            raise argparse.ArgumentTypeError
        return arg_value
    
    def _type_profile(self, arg_value, pat=re.compile(r"^[0-9a-zA-Z_.$#-]+$")):
        if not pat.match(arg_value):
            raise argparse.ArgumentTypeError
        return arg_value

    def _help(self, type):
        #Store text for help and other commands
        if type == "node":
            return "node[@subfolder][@folder]\nConnect to specific node or show all matching nodes\n[@subfolder][@folder]\nShow all available connections globaly or in specified path"
        if type == "usage":
            return "conn [-h] [--add | --del | --mod | --show | --debug] [node|folder]\n       conn {profile,move,mv,copy,cp,list,ls,bulk,config} ..."
        if type == "end":
            return "Commands:\n  profile        Manage profiles\n  move (mv)      Move node\n  copy (cp)      Copy node\n  list (ls)      List profiles, nodes or folders\n  bulk           Add nodes in bulk\n  run            Run scripts or commands on nodes\n  config         Manage app config\n  api            Start and stop connpy api"
        if type == "bashcompletion":
            return '''
#Here starts bash completion for conn
_conn()
{
        strings="$(connpy-completion-helper ${#COMP_WORDS[@]} ${COMP_WORDS[@]})"
        COMPREPLY=($(compgen -W "$strings" -- "${COMP_WORDS[-1]}"))
}
complete -o nosort -F _conn conn
complete -o nosort -F _conn connpy
#Here ends bash completion for conn
        '''
        if type == "zshcompletion":
            return '''

#Here starts zsh completion for conn
autoload -U compinit && compinit
_conn()
{
    strings=($(connpy-completion-helper ${#words} $words))
    compadd "$@" -- `echo $strings`
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

    def encrypt(self, password, keyfile=None):
        '''
        Encrypts password using RSA keyfile

        ### Parameters:  

            - password (str): Plaintext password to encrypt.

        ### Optional Parameters:  

            - keyfile  (str): Path/file to keyfile. Default is config keyfile.
                              

        ### Returns:  

            str: Encrypted password.

        '''
        if keyfile is None:
            keyfile = self.config.key
        with open(keyfile) as f:
            key = RSA.import_key(f.read())
            f.close()
        publickey = key.publickey()
        encryptor = PKCS1_OAEP.new(publickey)
        password = encryptor.encrypt(password.encode("utf-8"))
        return str(password)

