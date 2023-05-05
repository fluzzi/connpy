#!/usr/bin/env python3
#Imports
import json
import os
import re
from Crypto.PublicKey import RSA
from pathlib import Path
from copy import deepcopy


#functions and classes

class configfile:
    ''' This class generates a configfile object. Containts a dictionary storing, config, nodes and profiles, normaly used by connection manager.

    ### Attributes:  

        - file         (str): Path/file to config file.

        - key          (str): Path/file to RSA key file.

        - config      (dict): Dictionary containing information of connection
                              manager configuration.

        - connections (dict): Dictionary containing all the nodes added to
                              connection manager.

        - profiles    (dict): Dictionary containing all the profiles added to
                              connection manager.

        - privatekey   (obj): Object containing the private key to encrypt 
                              passwords.

        - publickey    (obj): Object containing the public key to decrypt 
                              passwords.
        '''

    def __init__(self, conf = None, key = None):
        ''' 
            
        ### Optional Parameters:  

            - conf (str): Path/file to config file. If left empty default
                          path is ~/.config/conn/config.json

            - key  (str): Path/file to RSA key file. If left empty default
                          path is ~/.config/conn/.osk

        '''
        home = os.path.expanduser("~")
        defaultdir = home + '/.config/conn'
        Path(defaultdir).mkdir(parents=True, exist_ok=True)
        pathfile = defaultdir + '/.folder'
        try:
            with open(pathfile, "r") as f:
                configdir = f.read().strip()
        except:
            with open(pathfile, "w") as f:
                f.write(str(defaultdir))
            configdir = defaultdir
        defaultfile = configdir + '/config.json'
        defaultkey = configdir + '/.osk'
        if conf == None:
            self.file = defaultfile
        else:
            self.file = conf
        if key == None:
            self.key = defaultkey
        else:
            self.key = key
        if os.path.exists(self.file):
            config = self._loadconfig(self.file)
        else:
            config = self._createconfig(self.file)
        self.config = config["config"]
        self.connections = config["connections"]
        self.profiles = config["profiles"]
        if not os.path.exists(self.key):
            self._createkey(self.key)
        with open(self.key) as f:
            self.privatekey = RSA.import_key(f.read())
            f.close()
        self.publickey = self.privatekey.publickey()


    def _loadconfig(self, conf):
        #Loads config file
        jsonconf = open(conf)
        jsondata = json.load(jsonconf)
        jsonconf.close()
        return jsondata

    def _createconfig(self, conf):
        #Create config file
        defaultconfig = {'config': {'case': False, 'idletime': 30, 'fzf': False}, 'connections': {}, 'profiles': { "default": { "host":"", "protocol":"ssh", "port":"", "user":"", "password":"", "options":"", "logs":"", "tags": "" }}}
        if not os.path.exists(conf):
            with open(conf, "w") as f:
                json.dump(defaultconfig, f, indent = 4)
                f.close()
                os.chmod(conf, 0o600)
        jsonconf = open(conf)
        jsondata = json.load(jsonconf)
        jsonconf.close()
        return jsondata

    def _saveconfig(self, conf):
        #Save config file
        newconfig = {"config":{}, "connections": {}, "profiles": {}}
        newconfig["config"] = self.config
        newconfig["connections"] = self.connections
        newconfig["profiles"] = self.profiles
        with open(conf, "w") as f:
            json.dump(newconfig, f, indent = 4)
            f.close()

    def _createkey(self, keyfile):
        #Create key file
        key = RSA.generate(2048)
        with open(keyfile,'wb') as f:
            f.write(key.export_key('PEM'))
            f.close()
            os.chmod(keyfile, 0o600)
        return key

    def _explode_unique(self, unique):
        #Divide unique name into folder, subfolder and id
        uniques = unique.split("@")
        if not unique.startswith("@"):
            result = {"id": uniques[0]}
        else:
            result = {}
        if len(uniques) == 2:
            result["folder"] = uniques[1]
            if result["folder"] == "":
                return False
        elif len(uniques) == 3:
            result["folder"] = uniques[2]
            result["subfolder"] = uniques[1]
            if result["folder"] == "" or result["subfolder"] == "":
                return False
        elif len(uniques) > 3:
            return False
        return result

    def getitem(self, unique, keys = None):
        '''
        Get an node or a group of nodes from configfile which can be passed to node/nodes class

        ### Parameters:  

            - unique (str): Unique name of the node or folder in config using
                            connection manager style: node[@subfolder][@folder]
                            or [@subfolder]@folder

        ### Optional Parameters:  

            - keys (list): In case you pass a folder as unique, you can filter
                           nodes inside the folder passing a list.

        ### Returns:  

            dict: Dictionary containing information of node or multiple 
                  dictionaries of multiple nodes.

        '''
        uniques = self._explode_unique(unique)
        if unique.startswith("@"):
            if uniques.keys() >= {"folder", "subfolder"}:
                folder = self.connections[uniques["folder"]][uniques["subfolder"]]
            else:
                folder = self.connections[uniques["folder"]]
            newfolder = deepcopy(folder)
            newfolder.pop("type")
            for node in folder.keys():
                if node == "type":
                    continue
                if "type" in newfolder[node].keys():
                    if newfolder[node]["type"] == "subfolder":
                        newfolder.pop(node)
                    else:
                        newfolder[node].pop("type")
            if keys == None:
                newfolder = {"{}{}".format(k,unique):v for k,v in newfolder.items()}
                return newfolder
            else:
                f_newfolder = dict((k, newfolder[k]) for k in keys)
                f_newfolder = {"{}{}".format(k,unique):v for k,v in f_newfolder.items()}
                return f_newfolder
        else:
            if uniques.keys() >= {"folder", "subfolder"}:
                node = self.connections[uniques["folder"]][uniques["subfolder"]][uniques["id"]]
            elif "folder" in uniques.keys():
                node = self.connections[uniques["folder"]][uniques["id"]]
            else:
                node = self.connections[uniques["id"]]
            newnode = deepcopy(node)
            newnode.pop("type")
            return newnode

    def getitems(self, uniques):
        '''
        Get a group of nodes from configfile which can be passed to node/nodes class

        ### Parameters:  

            - uniques (str/list): Regex string name that will match hostnames 
                                  from the connection manager. It can be a 
                                  list of strings.

        ### Returns:  

            dict: Dictionary containing information of node or multiple 
                  dictionaries of multiple nodes.

        '''
        nodes = {}
        for i in uniques:
            if isinstance(i, dict):
                name = list(i.keys())[0]
                mylist = i[name]
                if not self.config["case"]:
                    name = name.lower()
                    mylist = [item.lower() for item in mylist]
                this = self.getitem(name, mylist)
                nodes.update(this)
            elif i.startswith("@"):
                if not self.config["case"]:
                    i = i.lower()
                this = self.getitem(i)
                nodes.update(this)
            else:
                if not self.config["case"]:
                    i = i.lower()
                this = self.getitem(i)
                nodes[i] = this
        return nodes


    def _connections_add(self,*, id, host, folder='', subfolder='', options='', logs='', password='', port='', protocol='', user='', tags='', type = "connection" ):
        #Add connection from config
        if folder == '':
            self.connections[id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user, "tags": tags,"type": type}
        elif folder != '' and subfolder == '':
            self.connections[folder][id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user, "tags": tags, "type": type}
        elif folder != '' and subfolder != '':
            self.connections[folder][subfolder][id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user, "tags": tags, "type": type}
            

    def _connections_del(self,*, id, folder='', subfolder=''):
        #Delete connection from config
        if folder == '':
            del self.connections[id]
        elif folder != '' and subfolder == '':
            del self.connections[folder][id]
        elif folder != '' and subfolder != '':
            del self.connections[folder][subfolder][id]

    def _folder_add(self,*, folder, subfolder = ''):
        #Add Folder from config
        if subfolder == '':
            if folder not in self.connections:
                self.connections[folder] = {"type": "folder"}
        else:
            if subfolder not in self.connections[folder]:
                self.connections[folder][subfolder] = {"type": "subfolder"}

    def _folder_del(self,*, folder, subfolder=''):
        #Delete folder from config
        if subfolder == '':
            del self.connections[folder]
        else:
            del self.connections[folder][subfolder]


    def _profiles_add(self,*, id, host = '', options='', logs='', password='', port='', protocol='', user='', tags='' ):
        #Add profile from config
        self.profiles[id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user, "tags": tags}
            

    def _profiles_del(self,*, id ):
        #Delete profile from config
        del self.profiles[id]
        
    def _getallnodes(self, filter = None):
        #get all nodes on configfile
        nodes = []
        layer1 = [k for k,v in self.connections.items() if isinstance(v, dict) and v["type"] == "connection"]
        folders = [k for k,v in self.connections.items() if isinstance(v, dict) and v["type"] == "folder"]
        nodes.extend(layer1)
        for f in folders:
            layer2 = [k + "@" + f for k,v in self.connections[f].items() if isinstance(v, dict) and v["type"] == "connection"]
            nodes.extend(layer2)
            subfolders = [k for k,v in self.connections[f].items() if isinstance(v, dict) and v["type"] == "subfolder"]
            for s in subfolders:
                layer3 = [k + "@" + s + "@" + f for k,v in self.connections[f][s].items() if isinstance(v, dict) and v["type"] == "connection"]
                nodes.extend(layer3)
        if filter:
            if isinstance(filter, str):
                nodes = [item for item in nodes if re.search(filter, item)]
            elif isinstance(filter, list):
                nodes = [item for item in nodes if any(re.search(pattern, item) for pattern in filter)]
            else:
                raise ValueError("filter must be a string or a list of strings")
        return nodes

    def _getallnodesfull(self, filter = None):
        #get all nodes on configfile with all their attributes.
        nodes = {}
        layer1 = {k:v for k,v in self.connections.items() if isinstance(v, dict) and v["type"] == "connection"}
        folders = [k for k,v in self.connections.items() if isinstance(v, dict) and v["type"] == "folder"]
        nodes.update(layer1)
        for f in folders:
            layer2 = {k + "@" + f:v for k,v in self.connections[f].items() if isinstance(v, dict) and v["type"] == "connection"}
            nodes.update(layer2)
            subfolders = [k for k,v in self.connections[f].items() if isinstance(v, dict) and v["type"] == "subfolder"]
            for s in subfolders:
                layer3 = {k + "@" + s + "@" + f:v for k,v in self.connections[f][s].items() if isinstance(v, dict) and v["type"] == "connection"}
                nodes.update(layer3)
        if filter:
            if isinstance(filter, str):
                nodes = {k: v for k, v in nodes.items() if re.search(filter, k)}
            elif isinstance(filter, list):
                nodes = {k: v for k, v in nodes.items() if any(re.search(pattern, k) for pattern in filter)}
            else:
                raise ValueError("filter must be a string or a list of strings")
        for node, keys in nodes.items():
            for key, value in keys.items():
                profile = re.search("^@(.*)", str(value))
                if profile:
                    try:
                        nodes[node][key] = self.profiles[profile.group(1)][key]
                    except:
                        nodes[node][key] = ""
                elif value == '' and key == "protocol":
                    try:
                        nodes[node][key] = config.profiles["default"][key]
                    except:
                        nodes[node][key] = "ssh"
        return nodes


    def _getallfolders(self):
        #get all folders on configfile
        folders = ["@" + k for k,v in self.connections.items() if isinstance(v, dict) and v["type"] == "folder"]
        subfolders = []
        for f in folders:
            s = ["@" + k + f for k,v in self.connections[f[1:]].items() if isinstance(v, dict) and v["type"] == "subfolder"]
            subfolders.extend(s)
        folders.extend(subfolders)
        return folders

    def _profileused(self, profile):
        #Check if profile is used before deleting it
        nodes = []
        layer1 = [k for k,v in self.connections.items() if isinstance(v, dict) and v["type"] == "connection" and ("@" + profile in v.values() or ( isinstance(v["password"],list) and "@" + profile in v["password"]))]
        folders = [k for k,v in self.connections.items() if isinstance(v, dict) and v["type"] == "folder"]
        nodes.extend(layer1)
        for f in folders:
            layer2 = [k + "@" + f for k,v in self.connections[f].items() if isinstance(v, dict) and v["type"] == "connection" and ("@" + profile in v.values() or ( isinstance(v["password"],list) and "@" + profile in v["password"]))]
            nodes.extend(layer2)
            subfolders = [k for k,v in self.connections[f].items() if isinstance(v, dict) and v["type"] == "subfolder"]
            for s in subfolders:
                layer3 = [k + "@" + s + "@" + f for k,v in self.connections[f][s].items() if isinstance(v, dict) and v["type"] == "connection" and ("@" + profile in v.values() or ( isinstance(v["password"],list) and "@" + profile in v["password"]))]
                nodes.extend(layer3)
        return nodes

