#!/usr/bin/env python3
#Imports
import json
import os
import re
from Crypto.PublicKey import RSA
from pathlib import Path


#functions and classes

class configfile:
    
    def __init__(self, conf = None, *, key = None):
        home = os.path.expanduser("~")
        self.defaultdir = home + '/.config/conn'
        self.defaultfile = self.defaultdir + '/config.json'
        self.defaultkey = self.defaultdir + '/.osk'
        Path(self.defaultdir).mkdir(parents=True, exist_ok=True)
        if conf == None:
            self.file = self.defaultfile
        else:
            self.file = conf
        if key == None:
            self.key = self.defaultkey
        else:
            self.key = key
        if os.path.exists(self.file):
            config = self.loadconfig(self.file)
        else:
            config = self.createconfig(self.file)
        self.config = config["config"]
        self.connections = config["connections"]
        self.profiles = config["profiles"]
        if not os.path.exists(self.key):
            self.createkey(self.key)
        self.privatekey = RSA.import_key(open(self.key).read())
        self.publickey = self.privatekey.publickey()


    def loadconfig(self, conf):
        jsonconf = open(conf)
        return json.load(jsonconf)

    def createconfig(self, conf):
        defaultconfig = {'config': {'case': False, 'idletime': 30}, 'connections': {}, 'profiles': { "default": { "host":"", "protocol":"ssh", "port":"", "user":"", "password":"", "options":"", "logs":"" }}}
        if not os.path.exists(conf):
            with open(conf, "w") as f:
                json.dump(defaultconfig, f, indent = 4)
                f.close()
                os.chmod(conf, 0o600)
        jsonconf = open(conf)
        return json.load(jsonconf)

    def saveconfig(self, conf):
        newconfig = {"config":{}, "connections": {}, "profiles": {}}
        newconfig["config"] = self.config
        newconfig["connections"] = self.connections
        newconfig["profiles"] = self.profiles
        with open(conf, "w") as f:
            json.dump(newconfig, f, indent = 4)
            f.close()

    def createkey(self, keyfile):
        key = RSA.generate(2048)
        with open(keyfile,'wb') as f:
            f.write(key.export_key('PEM'))
            f.close()
            os.chmod(keyfile, 0o600)

    def _explode_unique(self, unique):
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
            uniques = self._explode_unique(unique)
            if unique.startswith("@"):
                if uniques.keys() >= {"folder", "subfolder"}:
                    folder = self.connections[uniques["folder"]][uniques["subfolder"]]
                else:
                    folder = self.connections[uniques["folder"]]
                newfolder = folder.copy()
                newfolder.pop("type")
                for node in newfolder.keys():
                    if "type" in newfolder[node].keys():
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
        if folder == '':
            self.connections[id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user, "type": type}
        elif folder != '' and subfolder == '':
            self.connections[folder][id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user, "type": type}
        elif folder != '' and subfolder != '':
            self.connections[folder][subfolder][id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user, "type": type}
            

    def _connections_del(self,*, id, folder='', subfolder=''):
        if folder == '':
            del self.connections[id]
        elif folder != '' and subfolder == '':
            del self.connections[folder][id]
        elif folder != '' and subfolder != '':
            del self.connections[folder][subfolder][id]

    def _folder_add(self,*, folder, subfolder = ''):
        if subfolder == '':
            if folder not in self.connections:
                self.connections[folder] = {"type": "folder"}
        else:
            if subfolder not in self.connections[folder]:
                self.connections[folder][subfolder] = {"type": "subfolder"}

    def _folder_del(self,*, folder, subfolder=''):
        if subfolder == '':
            del self.connections[folder]
        else:
            del self.connections[folder][subfolder]


    def _profiles_add(self,*, id, host = '', options='', logs='', password='', port='', protocol='', user='' ):
        self.profiles[id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user}
            

    def _profiles_del(self,*, id ):
        del self.profiles[id]
