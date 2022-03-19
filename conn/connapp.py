#!/usr/bin/env python3
#Imports
import os
import re
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
import ast
import argparse

#functions and classes

class connapp:

    def __init__(self, config, node):
        self.node = node
        self.config = config
        self.nodes = self._getallnodes()
        parser = argparse.ArgumentParser(prog = "conn", description = "SSH and Telnet connection manager", formatter_class=argparse.RawTextHelpFormatter)
        crud = parser.add_mutually_exclusive_group()
        parser.add_argument("node", metavar="node|folder", nargs='?', default=None, action=self.store_type, type=self._type, help="node[@subfolder][@folder]\nRecursively search in folders and subfolders if not specified\n[@subfolder][@folder]\nShow all available connections globaly or in specified path")
        crud.add_argument("--add", dest="action", action='append_const', help="Add new node[@subfolder][@folder]", const="add")
        crud.add_argument("--del", "--rm", dest="action", action='append_const', help="Delete node[@subfolder][@folder]", const="del")
        crud.add_argument("--mod", "--edit", dest="action", action='append_const', help="Modify node[@subfolder][@folder]", const="mod")
        crud.add_argument("--show", dest="action", action='append_const', help="Show node[@subfolder][@folder]", const="show")
        crud.add_argument("--mv", dest="action", nargs=2, action=self.store_type, help="Move node[@subfolder][@folder] dest_node[@subfolder][@folder]", default="mv", type=self._type)
        crud.add_argument("--cp", dest="action", nargs=2, action=self.store_type, help="Copy node[@subfolder][@folder] new_node[@subfolder][@folder]", default="cp", type=self._type)
        crud.add_argument("--ls", dest="action", action='store', choices=["nodes","folders"], help="List nodes or folders", default=False)
        crud.add_argument("--bulk", const="bulk", dest="action", action='append_const', help="Add nodes in bulk")
        self.parser = parser.parse_args()
        print(vars(self.parser))
        # if self.parser.node == False:
            # print("todos")
        # else:
            # print(self.parser.node)

    def _node_type(self, arg_value, pat=re.compile(r"^[0-9a-zA-Z_.$@#-]+$")):
        if not pat.match(arg_value):
            raise argparse.ArgumentTypeError
        uniques = self.config._explode_unique(arg_value)
        if uniques == False:
            raise argparse.ArgumentTypeError
        return uniques

    def _type(self, arg_value, pat=re.compile(r"^[0-9a-zA-Z_.$@#-]+$"), arg = "node"):
        if not pat.match(arg_value):
            raise argparse.ArgumentTypeError
        uniques = self.config._explode_unique(arg_value)
        if uniques == False:
            raise argparse.ArgumentTypeError
        if arg == "node":
            return uniques
        else:
            return arg
    
    class store_type(argparse.Action):
        def __call__(self, parser, args, values, option_string=None):
            result = [self.default]
            if values is not None:
                result.extend(values)
            setattr(args, self.dest, result)

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

    def encrypt(password, keyfile=None):
        if keyfile is None:
            home = os.path.expanduser("~")
            keyfile = home + '/.config/conn/.osk'
        key = RSA.import_key(open(keyfile).read())
        publickey = key.publickey()
        encryptor = PKCS1_OAEP.new(publickey)
        password = encryptor.encrypt(password.encode("utf-8"))
        return password
