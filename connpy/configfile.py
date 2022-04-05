#!/usr/bin/env python3
#Imports
import json
import os
import re
from Crypto.PublicKey import RSA
from pathlib import Path


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
        defaultfile = defaultdir + '/config.json'
        defaultkey = defaultdir + '/.osk'
        Path(defaultdir).mkdir(parents=True, exist_ok=True)
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
        self.privatekey = RSA.import_key(open(self.key).read())
        self.publickey = self.privatekey.publickey()


    def _loadconfig(self, conf):
        #Loads config file
        jsonconf = open(conf)
        return json.load(jsonconf)

    def _createconfig(self, conf):
        #Create config file
        defaultconfig = {'config': {'case': False, 'idletime': 30}, 'connections': {}, 'profiles': { "default": { "host":"", "protocol":"ssh", "port":"", "user":"", "password":"", "options":"", "logs":"" }}}
        if not os.path.exists(conf):
            with open(conf, "w") as f:
                json.dump(defaultconfig, f, indent = 4)
                f.close()
                os.chmod(conf, 0o600)
        jsonconf = open(conf)
        return json.load(jsonconf)

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

            dict: Dictionary containing information of node or multiple dictionaries
                  of multiple nodes.

        '''
        uniques = self._explode_unique(unique)
        if unique.startswith("@"):
            if uniques.keys() >= {"folder", "subfolder"}:
                folder = self.connections[uniques["folder"]][uniques["subfolder"]]
            else:
                folder = self.connections[uniques["folder"]]
            newfolder = folder.copy()
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
                return newfolder
            else:
                f_newfolder = dict((k, newfolder[k]) for k in keys)
                return f_newfolder
        else:
            if uniques.keys() >= {"folder", "subfolder"}:
                node = self.connections[uniques["folder"]][uniques["subfolder"]][uniques["id"]]
            elif "folder" in uniques.keys():
                node = self.connections[uniques["folder"]][uniques["id"]]
            else:
                node = self.connections[uniques["id"]]
            newnode = node.copy()
            newnode.pop("type")
            return newnode

    def _connections_add(self,*, id, host, folder='', subfolder='', options='', logs='', password='', port='', protocol='', user='', type = "connection" ):
        #Add connection from config
        if folder == '':
            self.connections[id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user, "type": type}
        elif folder != '' and subfolder == '':
            self.connections[folder][id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user, "type": type}
        elif folder != '' and subfolder != '':
            self.connections[folder][subfolder][id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user, "type": type}
            

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


    def _profiles_add(self,*, id, host = '', options='', logs='', password='', port='', protocol='', user='' ):
        #Add profile from config
        self.profiles[id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user}
            

    def _profiles_del(self,*, id ):
        #Delete profile from config
        del self.profiles[id]
