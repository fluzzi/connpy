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
import yaml

#functions and classes

class connapp:

    def __init__(self, config, node):
        self.node = node
        self.config = config
        self.nodes = self._getallnodes()
        self.folders = self._getallfolders()
        self.profiles = list(self.config.profiles.keys())
        #DEFAULTPARSER
        defaultparser = argparse.ArgumentParser(prog = "conn", description = "SSH and Telnet connection manager", formatter_class=argparse.RawTextHelpFormatter)
        subparsers = defaultparser.add_subparsers(title="Commands")
        #NODEPARSER
        nodeparser = subparsers.add_parser("node", help=self._help("node"),formatter_class=argparse.RawTextHelpFormatter) 
        nodecrud = nodeparser.add_mutually_exclusive_group()
        nodeparser.add_argument("node", metavar="node|folder", nargs='?', default=None, action=self.store_type, type=self._type_node, help=self._help("node"))
        nodecrud.add_argument("--add", dest="action", action="store_const", help="Add new node[@subfolder][@folder]", const="add", default="connect")
        nodecrud.add_argument("--del", "--rm", dest="action", action="store_const", help="Delete node[@subfolder][@folder]", const="del", default="connect")
        nodecrud.add_argument("--mod", "--edit", dest="action", action="store_const", help="Modify node[@subfolder][@folder]", const="mod", default="connect")
        nodecrud.add_argument("--show", dest="action", action="store_const", help="Show node[@subfolder][@folder]", const="show", default="connect")
        nodeparser.set_defaults(func=self._func_node)
        #PROFILEPARSER
        profileparser = subparsers.add_parser("profile", help="Manage profiles") 
        profileparser.add_argument("profile", nargs='?', action=self.store_type, type=self._type_profile, help="Name of profile to manage")
        profilecrud = profileparser.add_mutually_exclusive_group(required=True)
        profilecrud.add_argument("--add", dest="action", action="store_const", help="Add new profile", const="add", default="connect")
        profilecrud.add_argument("--del", "--rm", dest="action", action="store_const", help="Delete profile", const="del", default="connect")
        profilecrud.add_argument("--mod", "--edit", dest="action", action="store_const", help="Modify profile", const="mod", default="connect")
        profilecrud.add_argument("--show", dest="action", action="store_const", help="Show profile", const="show", default="connect")
        profileparser.set_defaults(func=self._func_profile)
        #MOVEPARSER
        moveparser = subparsers.add_parser("move", aliases=["mv"], help="Move node") 
        moveparser.add_argument("move", nargs=2, action=self.store_type, help="Move node[@subfolder][@folder] dest_node[@subfolder][@folder]", default="move", type=self._type_node)
        moveparser.set_defaults(func=self._func_others)
        #COPYPARSER
        copyparser = subparsers.add_parser("copy", aliases=["cp"], help="Copy node") 
        copyparser.add_argument("cp", nargs=2, action=self.store_type, help="Copy node[@subfolder][@folder] new_node[@subfolder][@folder]", default="cp", type=self._type_node)
        copyparser.set_defaults(func=self._func_others)
        #LISTPARSER
        lsparser = subparsers.add_parser("list", aliases=["ls"], help="List profiles, nodes or folders") 
        lsparser.add_argument("ls", action=self.store_type, choices=["profiles","nodes","folders"], help="List profiles, nodes or folders", default=False)
        lsparser.set_defaults(func=self._func_others)
        #BULKPARSER
        bulkparser = subparsers.add_parser("bulk", help="Add nodes in bulk") 
        bulkparser.add_argument("bulk", const="bulk", nargs=0, action=self.store_type, help="Add nodes in bulk")
        bulkparser.set_defaults(func=self._func_others)
        #Set default subparser and tune arguments
        commands = ["node", "-h", "--help", "profile", "mv", "move","copy", "cp", "bulk", "ls", "list"]
        profilecmds = ["--add", "--del", "--rm", "--mod", "--edit", "--show"]
        if len(sys.argv) >= 3 and sys.argv[2] == "profile" and sys.argv[1] in profilecmds:
            sys.argv[2] = sys.argv[1]
            sys.argv[1] = "profile"
        if len(sys.argv) < 2 or sys.argv[1] not in commands:
            sys.argv.insert(1,"node")
        args = defaultparser.parse_args()
        args.func(args)

    def _func_node(self, args):
        if args.action == "connect":
            if args.data == None:
                matches = self.nodes
            else:
                if args.data.startswith("@"):
                    matches = list(filter(lambda k: args.data in k, self.nodes))
                else:
                    matches = list(filter(lambda k: k.startswith(args.data), self.nodes))
            if len(matches) == 0:
                print("ERROR NO MACHEA NI FOLDER NI NODE")
                return
            elif len(matches) > 1:
                matches[0] = self._choose(matches,"node", "connect")
            if matches[0] == None:
                return
            node = self._get_item(matches[0])
            node = self.node(matches[0],**node, config = self.config)
            node.interact()
        elif args.action == "del":
            if args.data == None:
                print("MISSING ARGUMENT NODE")
                return
            elif args.data.startswith("@"):
                matches = list(filter(lambda k: k == args.data, self.folders))
            else:
                matches = list(filter(lambda k: k == args.data, self.nodes))
            if len(matches) == 0:
                print("ERROR NO MACHEO NI FOLDER NI NODE")
                return
            question = [inquirer.Confirm("delete", message="Are you sure you want to delete {}?".format(matches[0]))]
            confirm = inquirer.prompt(question)
            if confirm["delete"]:
                uniques = self.config._explode_unique(matches[0])
                if args.data.startswith("@"):
                    self.config._folder_del(**uniques)
                else:
                    self.config._connections_del(**uniques)
                self.config.saveconfig(self.config.file)
                print("{} deleted succesfully".format(matches[0]))
        elif args.action == "add":
            if args.data == None:
                print("MISSING ARGUMENT NODE")
                return
            elif args.data.startswith("@"):
                type = "folder"
                matches = list(filter(lambda k: k == args.data, self.folders))
            else:
                type = "node"
                matches = list(filter(lambda k: k == args.data, self.nodes))
            if len(matches) > 0:
                print(matches[0] + " ALLREADY EXIST")
                return
            else:
                if type == "folder":
                    uniques = self.config._explode_unique(args.data)
                    if uniques == False:
                        print("Invalid folder {}".format(args.data))
                        return
                    if "subfolder" in uniques.keys():
                        parent = "@" + uniques["folder"]
                        if parent not in self.folders:
                            print("FOLDER {} DONT EXIST".format(uniques["folder"]))
                            return
                    self.config._folder_add(**uniques)
                    self.config.saveconfig(self.config.file)
                    print("{} added succesfully".format(args.data))
                        
                if type == "node":
                    nodefolder = args.data.partition("@")
                    nodefolder = "@" + nodefolder[2]
                    if nodefolder not in self.folders and nodefolder != "@":
                        print(nodefolder + " DONT EXIST")
                        return
                    uniques = self.config._explode_unique(args.data)
                    if uniques == False:
                        print("Invalid node {}".format(args.data))
                        return False
                    print("You can use the configured setting in a profile using @profilename.")
                    print("You can also leave empty any value except hostname/IP.")
                    print("You can pass 1 or more passwords using comma separated @profiles")
                    print("You can use this variables on logging file name: ${id} ${unique} ${host} ${port} ${user} ${protocol}")
                    newnode = self._questions_nodes(args.data, args.action, uniques)
                    if newnode == False:
                        return
                    self.config._connections_add(**newnode)
                    self.config.saveconfig(self.config.file)
                    print("{} added succesfully".format(args.data))
        elif args.action == "show":
            if args.data == None:
                print("MISSING ARGUMENT NODE")
                return
            matches = list(filter(lambda k: k == args.data, self.nodes))
            if len(matches) == 0:
                print("ERROR NO MACHEO NODE")
                return
            node = self._get_item(matches[0])
            print(yaml.dump(node, Dumper=yaml.CDumper))
        elif args.action == "mod":
            if args.data == None:
                print("MISSING ARGUMENT NODE")
                return
            matches = list(filter(lambda k: k == args.data, self.nodes))
            if len(matches) == 0:
                print("ERROR NO MACHEO NODE")
                return
            node = self._get_item(matches[0])
            edits = self._questions_edit()
            if edits == None:
                return
            uniques = self.config._explode_unique(args.data)
            updatenode = self._questions_nodes(args.data, args.action, uniques, edit=edits)
            if not updatenode:
                return
            node.pop("type")
            uniques.update(node)
            if sorted(updatenode.items()) == sorted(uniques.items()):
                print("Nothing to do here")
                return
            else:
                self.config._connections_add(**updatenode)
                self.config.saveconfig(self.config.file)
                print("{} edited succesfully".format(args.data))



        else:
            print(matches)

    def _func_profile(self, args):
        print(args.command)
        print(vars(args))
    
    def _func_others(self, args):
        print(args.command)
        print(vars(args))

    def _choose(self, list, name, action):
        questions = [inquirer.List(name, message="Pick {} to {}:".format(name,action), choices=list)]
        answer = inquirer.prompt(questions)
        if answer == None:
            return
        else:
            return answer[name]

    def _host_validation(self, answers, current, regex = "^.+$"):
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Host cannot be empty")
        if current.startswith("@"):
            if current[1:] not in self.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        return True

    def _protocol_validation(self, answers, current, regex = "(^ssh$|^telnet$|^$|^@.+$)"):
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Pick between ssh, telnet, leave empty or @profile")
        if current.startswith("@"):
            if current[1:] not in self.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        return True

    def _port_validation(self, answers, current, regex = "(^[0-9]*$|^@.+$)"):
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Pick a port between 1-65535, @profile o leave empty")
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
        profiles = current.split(",")
        for i in profiles:
            if not re.match(regex, i) or i[1:] not in self.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(i))
        return True

    def _default_validation(self, answers, current):
        if current.startswith("@"):
            if current[1:] not in self.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        return True

    def _questions_edit(self):
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

    def _questions_nodes(self, unique, action,uniques = None, edit = None):
        try:
            defaults = self._get_item(unique)
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
                answer["password"] = self.encrypt(passa["password"])
            elif answer["password"] == "Profiles":
                passq = [(inquirer.Text("password", message="Set a @profile or a comma separated list of @profiles", validate=self._pass_validation))]
                passa = inquirer.prompt(passq)
                answer["password"] = passa["password"].split(",")
            elif answer["password"] == "No Password":
                answer["password"] = ""
        result = {**uniques, **answer, **node}
        return result

    def _get_item(self, unique):
            uniques = self.config._explode_unique(unique)
            if unique.startswith("@"):
                if uniques.keys() >= {"folder", "subfolder"}:
                    folder = self.config.connections[uniques["folder"]][uniques["subfolder"]]
                else:
                    folder = self.config.connections[uniques["folder"]]
                return folder
            else:
                if uniques.keys() >= {"folder", "subfolder"}:
                    node = self.config.connections[uniques["folder"]][uniques["subfolder"]][uniques["id"]]
                elif "folder" in uniques.keys():
                    node = self.config.connections[uniques["folder"]][uniques["id"]]
                else:
                    node = self.config.connections[uniques["id"]]
                return node



    def _type_node(self, arg_value, pat=re.compile(r"^[0-9a-zA-Z_.$@#-]+$")):
        if not pat.match(arg_value):
            raise argparse.ArgumentTypeError
        return arg_value
    
    def _type_profile(self, arg_value, pat=re.compile(r"^[0-9a-zA-Z_.$#-]+$")):
        if not pat.match(arg_value):
            raise argparse.ArgumentTypeError
        return arg_value

    class store_type(argparse.Action):
        def __call__(self, parser, args, values, option_string=None):
            setattr(args, "data", values)
            delattr(args,self.dest)
            setattr(args, "command", self.dest)

    def _help(self, type):
        if type == "node":
            return "node[@subfolder][@folder]\nConnect to specific node or show all matching nodes\n[@subfolder][@folder]\nShow all available connections globaly or in specified path"

    def _getallnodes(self):
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
        folders = ["@" + k for k,v in self.config.connections.items() if isinstance(v, dict) and v["type"] == "folder"]
        subfolders = []
        for f in folders:
            s = ["@" + k + f for k,v in self.config.connections[f[1:]].items() if isinstance(v, dict) and v["type"] == "subfolder"]
            subfolders.extend(s)
        folders.extend(subfolders)
        return folders

    def encrypt(self, password, keyfile=None):
        if keyfile is None:
            keyfile = self.config.key
        key = RSA.import_key(open(keyfile).read())
        publickey = key.publickey()
        encryptor = PKCS1_OAEP.new(publickey)
        password = encryptor.encrypt(password.encode("utf-8"))
        return password
