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
from .core import node


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
        self.config = config
        self.nodes = self._getallnodes()
        self.folders = self._getallfolders()
        self.profiles = list(self.config.profiles.keys())
        self.case = self.config.config["case"]
        #DEFAULTPARSER
        defaultparser = argparse.ArgumentParser(prog = "conn", description = "SSH and Telnet connection manager", formatter_class=argparse.RawTextHelpFormatter)
        subparsers = defaultparser.add_subparsers(title="Commands")
        #NODEPARSER
        nodeparser = subparsers.add_parser("node",usage=self._help("usage"), help=self._help("node"),epilog=self._help("end"), formatter_class=argparse.RawTextHelpFormatter) 
        nodecrud = nodeparser.add_mutually_exclusive_group()
        nodeparser.add_argument("node", metavar="node|folder", nargs='?', default=None, action=self._store_type, type=self._type_node, help=self._help("node"))
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
        #CONFIGPARSER
        configparser = subparsers.add_parser("config", help="Manage app config") 
        configparser.add_argument("--allow-uppercase", dest="case", nargs=1, action=self._store_type, help="Allow case sensitive names", choices=["true","false"])
        configparser.add_argument("--keepalive", dest="idletime", nargs=1, action=self._store_type, help="Set keepalive time in seconds, 0 to disable", type=int, metavar="INT")
        configparser.add_argument("--completion", dest="completion", nargs=1, choices=["bash","zsh"], action=self._store_type, help="Get terminal completion configuration for conn")
        configparser.set_defaults(func=self._func_others)
        #Set default subparser and tune arguments
        commands = ["node", "profile", "mv", "move","copy", "cp", "bulk", "ls", "list", "config"]
        profilecmds = ["--add", "-a", "--del", "--rm",  "-r", "--mod", "--edit", "-e", "--show", "-s"]
        if len(sys.argv) >= 3 and sys.argv[2] == "profile" and sys.argv[1] in profilecmds:
            sys.argv[2] = sys.argv[1]
            sys.argv[1] = "profile"
        if len(sys.argv) < 2 or sys.argv[1] not in commands:
            sys.argv.insert(1,"node")
        args = defaultparser.parse_args()
        args.func(args)

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
        if args.action == "connect" or args.action == "debug":
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
        elif args.action == "del":
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
            if confirm["delete"]:
                uniques = self.config._explode_unique(matches[0])
                if args.data.startswith("@"):
                    self.config._folder_del(**uniques)
                else:
                    self.config._connections_del(**uniques)
                self.config._saveconfig(self.config.file)
                print("{} deleted succesfully".format(matches[0]))
        elif args.action == "add":
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
                    newnode = self._questions_nodes(args.data, uniques)
                    if newnode == False:
                        exit(7)
                    self.config._connections_add(**newnode)
                    self.config._saveconfig(self.config.file)
                    print("{} added succesfully".format(args.data))
        elif args.action == "show":
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
        elif args.action == "mod":
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
        if args.action == "del":
            matches = list(filter(lambda k: k == args.data[0], self.profiles))
            if len(matches) == 0:
                print("{} not found".format(args.data[0]))
                exit(2)
            if matches[0] == "default":
                print("Can't delete default profile")
                exit(6)
            usedprofile = self._profileused(matches[0])
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
        elif args.action == "show":
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
        elif args.action == "add":
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
        elif args.action == "mod":
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
        if args.command == "ls":
            print(*getattr(self, args.data), sep="\n")
        elif args.command == "move" or args.command == "cp":
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
            if args.command == "move":
                print("{} moved succesfully to {}".format(args.data[0],args.data[1]))
            if args.command == "cp":
                print("{} copied succesfully to {}".format(args.data[0],args.data[1]))
        elif args.command == "bulk":
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
                newnode["user"] = newnodes["user"]
                newnode["password"] = newnodes["password"]
                count +=1
                self.config._connections_add(**newnode)
                self.nodes = self._getallnodes()
            if count > 0:
                self.config._saveconfig(self.config.file)
                print("Succesfully added {} nodes".format(count))
            else:
                print("0 nodes added")
        else:
            if args.command == "completion":
                if args.data[0] == "bash":
                    print(self._help("bashcompletion"))
                elif args.data[0] == "zsh":
                    print(self._help("zshcompletion"))
            else:
                if args.command == "case":
                    if args.data[0] == "true":
                        args.data[0] = True
                    elif args.data[0] == "false":
                        args.data[0] = False
                if args.command == "idletime":
                    if args.data[0] < 0:
                        args.data[0] = 0
                self.config.config[args.command] = args.data[0]
                self.config._saveconfig(self.config.file)
                print("Config saved")

    def _choose(self, list, name, action):
        #Generates an inquirer list to pick
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
        questions.append(inquirer.Confirm("user", message="Edit User?"))
        questions.append(inquirer.Confirm("password", message="Edit password?"))
        answers = inquirer.prompt(questions)
        return answers

    def _questions_nodes(self, unique, uniques = None, edit = None):
        #Questions when adding or editing nodes
        try:
            defaults = self.config.getitem(unique)
        except:
            defaults = { "host":"", "protocol":"", "port":"", "user":"", "options":"", "logs":"" }
        node = {}

        if edit == None:
            edit = { "host":True, "protocol":True, "port":True, "user":True, "password": True,"options":True, "logs":True }
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
            questions.append(inquirer.Text("logs", message="Pick logging path/file ", validate=self._default_validation, default=defaults["logs"]))
        else:
            node["logs"] = defaults["logs"]
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
        result = {**uniques, **answer, **node}
        result["type"] = "connection"
        return result

    def _questions_profiles(self, unique, edit = None):
        #Questions when adding or editing profiles
        try:
            defaults = self.config.profiles[unique]
        except:
            defaults = { "host":"", "protocol":"", "port":"", "user":"", "options":"", "logs":"" }
        profile = {}
        if edit == None:
            edit = { "host":True, "protocol":True, "port":True, "user":True, "password": True,"options":True, "logs":True }
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
            questions.append(inquirer.Text("logs", message="Pick logging path/file ", default=defaults["logs"]))
        else:
            profile["logs"] = defaults["logs"]
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
            return "Commands:\n  profile        Manage profiles\n  move (mv)      Move node\n  copy (cp)      Copy node\n  list (ls)      List profiles, nodes or folders\n  bulk           Add nodes in bulk\n  config         Manage app config"
        if type == "bashcompletion":
            return '''
#Here starts bash completion for conn
#You need jq installed in order to use this
_conn()
{

    DATADIR=$HOME/.config/conn
    command -v jq >/dev/null 2>&1 && {
    mapfile -t connections < <(jq -r ' .["connections"] | paths as $path | select(getpath($path) == "connection") | $path |  [map(select(. != "type"))[-1,-2,-3]] | map(select(. !=null)) | join("@")' $DATADIR/config.json)
    mapfile -t folders < <(jq -r ' .["connections"] | paths as $path | select(getpath($path) == "folder" or getpath($path) == "subfolder") | $path | [map(select(. != "type"))[-1,-2]] | map(select(. !=null)) | join("@")' $DATADIR/config.json)
    mapfile -t profiles < <(jq -r '.["profiles"] | keys[]' $DATADIR/config.json)
    }
  if [ "${#COMP_WORDS[@]}" = "2" ]; then
          strings="--add --del --rm --edit --mod --show mv move ls list cp copy profile bulk config --help"
          strings="$strings ${connections[@]} ${folders[@]/#/@}"
          COMPREPLY=($(compgen -W "$strings" -- "${COMP_WORDS[1]}"))
  fi
  if [ "${#COMP_WORDS[@]}" = "3" ]; then
          strings=""
          if [ "${COMP_WORDS[1]}" = "profile" ]; then strings="--add --rm --del --edit --mod --show --help"; fi
          if [ "${COMP_WORDS[1]}" = "config" ]; then strings="--allow-uppercase --keepalive --completion --help"; fi
          if [[ "${COMP_WORDS[1]}" =~ --mod|--edit|-e|--show|-s|--add|-a|--rm|--del|-r ]]; then strings="profile"; fi
          if [[ "${COMP_WORDS[1]}" =~ list|ls ]]; then strings="profiles nodes folders"; fi
          if [[ "${COMP_WORDS[1]}" =~ bulk|mv|move|cp|copy ]]; then strings="--help"; fi
          if [[ "${COMP_WORDS[1]}" =~ --rm|--del|-r ]]; then strings="$strings ${folders[@]/#/@}"; fi
          if [[ "${COMP_WORDS[1]}" =~ --rm|--del|-r|--mod|--edit|-e|mv|move|cp|copy|--show|-s ]]; then
              strings="$strings ${connections[@]}"
          fi
          COMPREPLY=($(compgen -W "$strings" -- "${COMP_WORDS[2]}"))
  fi
  if [ "${#COMP_WORDS[@]}" = "4" ]; then
          strings=""
          if [ "${COMP_WORDS[1]}" = "profile" ]; then
                if [[ "${COMP_WORDS[2]}" =~ --rm|--del|-r|--mod|--edit|-e|--show|-s ]] ; then
                          strings="$strings ${profiles[@]}"
                fi
          fi
          if [ "${COMP_WORDS[2]}" = "profile" ]; then
                if [[ "${COMP_WORDS[1]}" =~ --rm|--del|-r|--mod|--edit|-e|--show|-s ]] ; then
                          strings="$strings ${profiles[@]}"
                fi
          fi
          if [ "${COMP_WORDS[1]}" = "config" ]; then
                if [[ "${COMP_WORDS[2]}" = "--completion" ]] ; then
                          strings="bash zsh"
                fi
          fi
          COMPREPLY=($(compgen -W "$strings" -- "${COMP_WORDS[3]}"))
  fi
}
complete -o nosort -F _conn conn
complete -o nosort -F _conn connpy
        '''
        if type == "zshcompletion":
            return '''
#Here starts zsh completion for conn
#You need jq installed in order to use this

autoload -U compinit && compinit
_conn()
{
    DATADIR=$HOME/.config/conn
    local COMP_WORDS num
    COMP_WORDS=( $words )
    num=${#COMP_WORDS[@]}
    if [[ $words =~ '.* $' ]]; then
        num=$(($num + 1))
    fi
    command -v jq >/dev/null 2>&1 && {
    x=`jq -r ' .["connections"] | paths as $path | select(getpath($path) == "connection") | $path |  [map(select(. != "type"))[-1,-2,-3]] | map(select(. !=null)) | join("@")'  $DATADIR/config.json`
    connections=( $x )
    x=`jq -r ' .["connections"] | paths as $path | select(getpath($path) == "folder" or getpath($path) == "subfolder") | $path | [map(select(. != "type"))[-1,-2]] | map(select(. !=null)) | join("@")' $DATADIR/config.json | sed -e 's/^/@/'`
    folders=( $x )
    x=`jq -r '.["profiles"] | keys[]' $DATADIR/config.json`
    profiles=( $x )
    }
  if [ "${num}" = "2" ]; then
          strings="--add --del --rm --edit --mod --show mv move ls list cp copy profile bulk config --help"
          strings="$strings ${connections[@]} ${folders[@]}"
          compadd "$@" -- `echo $strings`
  fi
  if [ "${num}" = "3" ]; then
          strings=""
          if [ "${COMP_WORDS[2]}" = "profile" ]; then strings="--add --rm --del --edit --mod --show --help"; fi
          if [ "${COMP_WORDS[2]}" = "config" ]; then strings="--allow-uppercase --keepalive --completion --help"; fi
          if [[ "${COMP_WORDS[2]}" =~ '--mod|--edit|-e|--show|-s|--add|-a|--rm|--del|-r' ]]; then strings="profile"; fi
          if [[ "${COMP_WORDS[2]}" =~ 'list|ls' ]]; then strings="profiles nodes folders"; fi
          if [[ "${COMP_WORDS[2]}" =~ 'bulk|mv|move|cp|copy' ]]; then strings="--help"; fi
          if [[ "${COMP_WORDS[2]}" =~ '--rm|--del|-r' ]]; then strings="$strings ${folders[@]}"; fi
          if [[ "${COMP_WORDS[2]}" =~ '--rm|--del|-r|--mod|--edit|-e|mv|move|cp|copy|--show|-s' ]]; then
              strings="$strings ${connections[@]}"
          fi
          compadd "$@" -- `echo $strings`
  fi
  if [ "${num}" = "4" ]; then
          strings=""
          if [ "${COMP_WORDS[2]}" = "profile" ]; then
                if [[ "${COMP_WORDS[3]}" =~ '--rm|--del|-r|--mod|--edit|-e|--show|-s' ]] ; then
                          strings="$strings ${profiles[@]}"
                fi
          fi
          if [ "${COMP_WORDS[3]}" = "profile" ]; then
                if [[ "${COMP_WORDS[2]}" =~ '--rm|--del|-r|--mod|--edit|-e|--show|-s' ]] ; then
                          strings="$strings ${profiles[@]}"
                fi
          fi
          if [ "${COMP_WORDS[2]}" = "config" ]; then
                if [[ "${COMP_WORDS[3]}" = '--completion' ]] ; then
                          strings="bash zsh"
                fi
          fi

          compadd "$@" -- `echo $strings`
  fi
}
compdef _conn conn
compdef _conn connpy
            '''

    def _getallnodes(self):
        #get all nodes on configfile
        nodes = []
        layer1 = [k for k,v in self.config.connections.items() if isinstance(v, dict) and v["type"] == "connection"]
        folders = [k for k,v in self.config.connections.items() if isinstance(v, dict) and v["type"] == "folder"]
        nodes.extend(layer1)
        for f in folders:
            layer2 = [k + "@" + f for k,v in self.config.connections[f].items() if isinstance(v, dict) and v["type"] == "connection"]
            nodes.extend(layer2)
            subfolders = [k for k,v in self.config.connections[f].items() if isinstance(v, dict) and v["type"] == "subfolder"]
            for s in subfolders:
                layer3 = [k + "@" + s + "@" + f for k,v in self.config.connections[f][s].items() if isinstance(v, dict) and v["type"] == "connection"]
                nodes.extend(layer3)
        return nodes

    def _getallfolders(self):
        #get all folders on configfile
        folders = ["@" + k for k,v in self.config.connections.items() if isinstance(v, dict) and v["type"] == "folder"]
        subfolders = []
        for f in folders:
            s = ["@" + k + f for k,v in self.config.connections[f[1:]].items() if isinstance(v, dict) and v["type"] == "subfolder"]
            subfolders.extend(s)
        folders.extend(subfolders)
        return folders

    def _profileused(self, profile):
        #Check if profile is used before deleting it
        nodes = []
        layer1 = [k for k,v in self.config.connections.items() if isinstance(v, dict) and v["type"] == "connection" and ("@" + profile in v.values() or ( isinstance(v["password"],list) and "@" + profile in v["password"]))]
        folders = [k for k,v in self.config.connections.items() if isinstance(v, dict) and v["type"] == "folder"]
        nodes.extend(layer1)
        for f in folders:
            layer2 = [k + "@" + f for k,v in self.config.connections[f].items() if isinstance(v, dict) and v["type"] == "connection" and ("@" + profile in v.values() or ( isinstance(v["password"],list) and "@" + profile in v["password"]))]
            nodes.extend(layer2)
            subfolders = [k for k,v in self.config.connections[f].items() if isinstance(v, dict) and v["type"] == "subfolder"]
            for s in subfolders:
                layer3 = [k + "@" + s + "@" + f for k,v in self.config.connections[f][s].items() if isinstance(v, dict) and v["type"] == "connection" and ("@" + profile in v.values() or ( isinstance(v["password"],list) and "@" + profile in v["password"]))]
                nodes.extend(layer3)
        return nodes

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
        key = RSA.import_key(open(keyfile).read())
        publickey = key.publickey()
        encryptor = PKCS1_OAEP.new(publickey)
        password = encryptor.encrypt(password.encode("utf-8"))
        return str(password)

