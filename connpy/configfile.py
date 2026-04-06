#!/usr/bin/env python3
#Imports
import json
import os
import re
import yaml
import shutil
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
from pathlib import Path
from copy import deepcopy
from .hooks import MethodHook, ClassHook
from . import printer



#functions and classes

@ClassHook
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
                          path is ~/.config/conn/config.yaml

            - key  (str): Path/file to RSA key file. If left empty default
                          path is ~/.config/conn/.osk

        '''
        home = os.path.expanduser("~")
        defaultdir = home + '/.config/conn'
        
        if conf is None:
            # Standard path: use ~/.config/conn and respect .folder redirection
            self.anchor_path = defaultdir
            self.defaultdir = defaultdir
            Path(defaultdir).mkdir(parents=True, exist_ok=True)
            
            pathfile = defaultdir + '/.folder'
            try:
                with open(pathfile, "r") as f:
                    configdir = f.read().strip()
            except (FileNotFoundError, IOError):
                with open(pathfile, "w") as f:
                    f.write(str(defaultdir))
                configdir = defaultdir
            
            self.defaultdir = configdir
            self.file = configdir + '/config.yaml'
            self.key = key or (configdir + '/.osk')

            # Ensure redirected directories exist
            Path(configdir).mkdir(parents=True, exist_ok=True)
            Path(f"{configdir}/plugins").mkdir(parents=True, exist_ok=True)
            
            # Backwards compatibility: Migrate from JSON to YAML only for default path
            legacy_json = configdir + '/config.json'
            legacy_noext = configdir + '/config'
            legacy_file = None
            if os.path.exists(legacy_json): legacy_file = legacy_json
            elif os.path.exists(legacy_noext): legacy_file = legacy_noext
            
            if not os.path.exists(self.file) and legacy_file:
                try:
                    with open(legacy_file, 'r') as f:
                        old_data = json.load(f)
                    if not self._validate_config(old_data):
                        printer.warning(f"Legacy config {legacy_file} has invalid structure, skipping migration.")
                    else:
                        with open(self.file, 'w') as f:
                            yaml.dump(old_data, f, default_flow_style=False, sort_keys=False)
                        # Verify the written YAML can be read back correctly
                        with open(self.file, 'r') as f:
                            verify = yaml.safe_load(f)
                        if not self._validate_config(verify):
                            os.remove(self.file)
                            printer.warning("YAML verification failed after migration, keeping legacy config.")
                        else:
                            # Note: cachefile is derived later, we use temp one for migration sync
                            temp_cache = configdir + '/.config.cache.json'
                            with open(temp_cache, 'w') as f:
                                json.dump(old_data, f)
                            shutil.move(legacy_file, legacy_file + ".backup")
                            printer.success(f"Migrated legacy config ({len(old_data.get('connections',{}))} folders/nodes) into YAML and Cache successfully!")
                except Exception as e:
                    if os.path.exists(self.file):
                        try: os.remove(self.file)
                        except OSError: pass
                    printer.warning(f"Failed to migrate legacy config: {e}")
        else:
            # Custom path (common in tests): isolate everything to the conf parent directory
            self.file = os.path.abspath(conf)
            configdir = os.path.dirname(self.file)
            self.anchor_path = configdir
            self.defaultdir = configdir
            self.key = os.path.abspath(key) if key else (configdir + '/.osk')

        # Sidecar files always live next to the config file (or in the redirected configdir)
        self.cachefile = configdir + '/.config.cache.json'
        self.fzf_cachefile = configdir + '/.fzf_nodes_cache.txt'
        self.folders_cachefile = configdir + '/.folders_cache.txt'
        self.profiles_cachefile = configdir + '/.profiles_cache.txt'
            
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
        self.publickey = self.privatekey.publickey()

        # Self-heal text caches if they are missing
        if not os.path.exists(self.fzf_cachefile) or not os.path.exists(self.folders_cachefile) or not os.path.exists(self.profiles_cachefile):
            self._generate_nodes_cache()


    def _validate_config(self, data):
        """Verify config data has the required structure."""
        if not isinstance(data, dict):
            return False
        required = {"config", "connections", "profiles"}
        return required.issubset(data.keys())

    def _loadconfig(self, conf):
        #Loads config file using dual cache
        cache_exists = os.path.exists(self.cachefile)
        yaml_time = os.path.getmtime(conf) if os.path.exists(conf) else 0
        cache_time = os.path.getmtime(self.cachefile) if cache_exists else 0

        if not cache_exists or yaml_time > cache_time:
            with open(conf, 'r') as f:
                data = yaml.safe_load(f)
            if not self._validate_config(data):
                # YAML is broken, try to recover from cache
                if cache_exists:
                    printer.warning("Config file appears corrupt, recovering from cache...")
                    with open(self.cachefile, 'r') as f:
                        data = json.load(f)
                    if self._validate_config(data):
                        # Re-write the YAML from good cache
                        with open(conf, 'w') as f:
                            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
                        return data
                # Both broken or no cache - create fresh
                printer.error("Config file is corrupt and no valid cache exists. Creating default config.")
                return self._createconfig(conf)
            try:
                with open(self.cachefile, 'w') as f:
                    json.dump(data, f)
            except Exception:
                pass
            return data
        else:
            with open(self.cachefile, 'r') as f:
                data = json.load(f)
            if not self._validate_config(data):
                # Cache broken, try yaml
                with open(conf, 'r') as f:
                    data = yaml.safe_load(f)
                if self._validate_config(data):
                    return data
                # Both broken
                printer.error("Both config and cache are corrupt. Creating default config.")
                return self._createconfig(conf)
            return data

    def _createconfig(self, conf):
        #Create config file (always writes defaults, safe for recovery)
        defaultconfig = {'config': {'case': False, 'idletime': 30, 'fzf': False}, 'connections': {}, 'profiles': { "default": { "host":"", "protocol":"ssh", "port":"", "user":"", "password":"", "options":"", "logs":"", "tags": "", "jumphost":""}}}
        with open(conf, "w") as f:
            yaml.dump(defaultconfig, f, default_flow_style=False, sort_keys=False)
        os.chmod(conf, 0o600)
        try:
            with open(self.cachefile, 'w') as f:
                json.dump(defaultconfig, f)
        except Exception:
            pass
        return defaultconfig

    @MethodHook
    def _saveconfig(self, conf):
        #Save config file atomically to prevent corruption
        newconfig = {"config":{}, "connections": {}, "profiles": {}}
        newconfig["config"] = self.config
        newconfig["connections"] = self.connections
        newconfig["profiles"] = self.profiles
        tmpfile = conf + '.tmp'
        try:
            with open(tmpfile, "w") as f:
                yaml.dump(newconfig, f, default_flow_style=False, sort_keys=False)
            # Atomic replace: only overwrite original if write succeeded
            shutil.move(tmpfile, conf)
            with open(self.cachefile, "w") as f:
                json.dump(newconfig, f)
            self._generate_nodes_cache()
        except (IOError, OSError) as e:
            printer.error(f"Failed to save config: {e}")
            # Clean up temp file if it exists
            if os.path.exists(tmpfile):
                try:
                    os.remove(tmpfile)
                except OSError:
                    pass
            return 1
        return 0

    def _generate_nodes_cache(self):
        try:
            nodes = self._getallnodes()
            folders = self._getallfolders()
            profiles = list(self.profiles.keys())
            
            with open(self.fzf_cachefile, "w") as f:
                f.write("\n".join(nodes))
            with open(self.folders_cachefile, "w") as f:
                f.write("\n".join(folders))
            with open(self.profiles_cachefile, "w") as f:
                f.write("\n".join(profiles))
        except Exception:
            pass

    def _createkey(self, keyfile):
        #Create key file
        key = RSA.generate(2048)
        with open(keyfile,'wb') as f:
            f.write(key.export_key('PEM'))
            f.close()
            os.chmod(keyfile, 0o600)
        return key

    @MethodHook
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

    @MethodHook
    def getitem(self, unique, keys = None, extract = False):
        '''
        Get an node or a group of nodes from configfile which can be passed to node/nodes class

        ### Parameters:  

            - unique (str): Unique name of the node or folder in config using
                            connection manager style: node[@subfolder][@folder]
                            or [@subfolder]@folder

        ### Optional Parameters:  

            - keys (list): In case you pass a folder as unique, you can filter
                           nodes inside the folder passing a list.
            - extract (bool): If True, extract information from profiles. 
                              Default False.

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
            for node_name in folder.keys():
                if node_name == "type":
                    continue
                if "type" in newfolder[node_name].keys():
                    if newfolder[node_name]["type"] == "subfolder":
                        newfolder.pop(node_name)
                    else:
                        newfolder[node_name].pop("type")
            
            if keys != None:
                newfolder = dict((k, newfolder[k]) for k in keys)
            
            if extract:
                for node_name, node_keys in newfolder.items():
                    for key, value in node_keys.items():
                        profile = re.search("^@(.*)", str(value))
                        if profile:
                            try:
                                newfolder[node_name][key] = self.profiles[profile.group(1)][key]
                            except KeyError:
                                newfolder[node_name][key] = ""
                        elif value == '' and key == "protocol":
                            try:
                                newfolder[node_name][key] = self.profiles["default"][key]
                            except KeyError:
                                newfolder[node_name][key] = "ssh"
            
            newfolder = {"{}{}".format(k,unique):v for k,v in newfolder.items()}
            return newfolder
        else:
            if uniques.keys() >= {"folder", "subfolder"}:
                node = self.connections[uniques["folder"]][uniques["subfolder"]][uniques["id"]]
            elif "folder" in uniques.keys():
                node = self.connections[uniques["folder"]][uniques["id"]]
            else:
                node = self.connections[uniques["id"]]
            newnode = deepcopy(node)
            newnode.pop("type")
            
            if extract:
                for key, value in newnode.items():
                    profile = re.search("^@(.*)", str(value))
                    if profile:
                        try:
                            newnode[key] = self.profiles[profile.group(1)][key]
                        except KeyError:
                            newnode[key] = ""
                    elif value == '' and key == "protocol":
                        try:
                            newnode[key] = self.profiles["default"][key]
                        except KeyError:
                            newnode[key] = "ssh"
            return newnode

    @MethodHook
    def getitems(self, uniques, extract = False):
        '''
        Get a group of nodes from configfile which can be passed to node/nodes class

        ### Parameters:  

            - uniques (str/list): String name that will match hostnames 
                                  from the connection manager. It can be a 
                                  list of strings.

        ### Optional Parameters:

            - extract (bool): If True, extract information from profiles. 
                              Default False.

        ### Returns:  

            dict: Dictionary containing information of node or multiple 
                  dictionaries of multiple nodes.

        '''
        nodes = {}
        if isinstance(uniques, str):
            uniques = [uniques]
        for i in uniques:
            if isinstance(i, dict):
                name = list(i.keys())[0]
                mylist = i[name]
                if not self.config["case"]:
                    name = name.lower()
                    mylist = [item.lower() for item in mylist]
                this = self.getitem(name, mylist, extract = extract)
                nodes.update(this)
            elif i.startswith("@"):
                if not self.config["case"]:
                    i = i.lower()
                this = self.getitem(i, extract = extract)
                nodes.update(this)
            else:
                if not self.config["case"]:
                    i = i.lower()
                this = self.getitem(i, extract = extract)
                nodes[i] = this
        return nodes


    @MethodHook
    def _connections_add(self,*, id, host, folder='', subfolder='', options='', logs='', password='', port='', protocol='', user='', tags='', jumphost='', type = "connection" ):
        #Add connection from config
        if folder == '':
            self.connections[id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user, "tags": tags,"jumphost": jumphost,"type": type}
        elif folder != '' and subfolder == '':
            self.connections[folder][id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user, "tags": tags, "jumphost": jumphost, "type": type}
        elif folder != '' and subfolder != '':
            self.connections[folder][subfolder][id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user, "tags": tags,  "jumphost": jumphost, "type": type}
            

    @MethodHook
    def _connections_del(self,*, id, folder='', subfolder=''):
        #Delete connection from config
        if folder == '':
            del self.connections[id]
        elif folder != '' and subfolder == '':
            del self.connections[folder][id]
        elif folder != '' and subfolder != '':
            del self.connections[folder][subfolder][id]

    @MethodHook
    def _folder_add(self,*, folder, subfolder = ''):
        #Add Folder from config
        if subfolder == '':
            if folder not in self.connections:
                self.connections[folder] = {"type": "folder"}
        else:
            if subfolder not in self.connections[folder]:
                self.connections[folder][subfolder] = {"type": "subfolder"}

    @MethodHook
    def _folder_del(self,*, folder, subfolder=''):
        #Delete folder from config
        if subfolder == '':
            del self.connections[folder]
        else:
            del self.connections[folder][subfolder]


    @MethodHook
    def _profiles_add(self,*, id, host = '', options='', logs='', password='', port='', protocol='', user='', tags='', jumphost='' ):
        #Add profile from config
        self.profiles[id] = {"host": host, "options": options, "logs": logs, "password": password, "port": port, "protocol": protocol, "user": user, "tags": tags, "jumphost": jumphost}
            

    @MethodHook
    def _profiles_del(self,*, id ):
        #Delete profile from config
        del self.profiles[id]
        
    @MethodHook
    def _getallnodes(self, filter = None):
        #get all nodes on configfile
        nodes = []
        layer1 = [k for k,v in self.connections.items() if isinstance(v, dict) and v.get("type") == "connection"]
        folders = [k for k,v in self.connections.items() if isinstance(v, dict) and v.get("type") == "folder"]
        nodes.extend(layer1)
        for f in folders:
            layer2 = [k + "@" + f for k,v in self.connections[f].items() if isinstance(v, dict) and v.get("type") == "connection"]
            nodes.extend(layer2)
            subfolders = [k for k,v in self.connections[f].items() if isinstance(v, dict) and v.get("type") == "subfolder"]
            for s in subfolders:
                layer3 = [k + "@" + s + "@" + f for k,v in self.connections[f][s].items() if isinstance(v, dict) and v.get("type") == "connection"]
                nodes.extend(layer3)
        if filter:
            if isinstance(filter, str):
                nodes = [item for item in nodes if re.search(filter, item)]
            elif isinstance(filter, list):
                nodes = [item for item in nodes if any(re.search(pattern, item) for pattern in filter)]
            else:
                raise ValueError("filter must be a string or a list of strings")
        return nodes

    @MethodHook
    def _getallnodesfull(self, filter = None, extract = True):
        #get all nodes on configfile with all their attributes.
        nodes = {}
        layer1 = {k:v for k,v in self.connections.items() if isinstance(v, dict) and v.get("type") == "connection"}
        folders = [k for k,v in self.connections.items() if isinstance(v, dict) and v.get("type") == "folder"]
        nodes.update(layer1)
        for f in folders:
            layer2 = {k + "@" + f:v for k,v in self.connections[f].items() if isinstance(v, dict) and v.get("type") == "connection"}
            nodes.update(layer2)
            subfolders = [k for k,v in self.connections[f].items() if isinstance(v, dict) and v.get("type") == "subfolder"]
            for s in subfolders:
                layer3 = {k + "@" + s + "@" + f:v for k,v in self.connections[f][s].items() if isinstance(v, dict) and v.get("type") == "connection"}
                nodes.update(layer3)
        if filter:
            if isinstance(filter, str):
                filter = "^(?!.*@).+$" if filter == "@" else filter
                nodes = {k: v for k, v in nodes.items() if re.search(filter, k)}
            elif isinstance(filter, list):
                filter = ["^(?!.*@).+$" if item == "@" else item for item in filter]
                nodes = {k: v for k, v in nodes.items() if any(re.search(pattern, k) for pattern in filter)}
            else:
                raise ValueError("filter must be a string or a list of strings")
        if extract:
            for node, keys in nodes.items():
                for key, value in keys.items():
                    profile = re.search("^@(.*)", str(value))
                    if profile:
                        try:
                            nodes[node][key] = self.profiles[profile.group(1)][key]
                        except KeyError:
                            nodes[node][key] = ""
                    elif value == '' and key == "protocol":
                        try:
                            nodes[node][key] = self.profiles["default"][key]
                        except KeyError:
                            nodes[node][key] = "ssh"
        return nodes


    @MethodHook
    def _getallfolders(self):
        #get all folders on configfile
        folders = ["@" + k for k,v in self.connections.items() if isinstance(v, dict) and v.get("type") == "folder"]
        subfolders = []
        for f in folders:
            s = ["@" + k + f for k,v in self.connections[f[1:]].items() if isinstance(v, dict) and v.get("type") == "subfolder"]
            subfolders.extend(s)
        folders.extend(subfolders)
        return folders

    @MethodHook
    def _profileused(self, profile):
        #Return all the nodes that uses this profile.
        nodes = []
        layer1 = [k for k,v in self.connections.items() if isinstance(v, dict) and v.get("type") == "connection" and ("@" + profile in v.values() or ( isinstance(v.get("password"),list) and "@" + profile in v.get("password")))]
        folders = [k for k,v in self.connections.items() if isinstance(v, dict) and v.get("type") == "folder"]
        nodes.extend(layer1)
        for f in folders:
            layer2 = [k + "@" + f for k,v in self.connections[f].items() if isinstance(v, dict) and v.get("type") == "connection" and ("@" + profile in v.values() or ( isinstance(v.get("password"),list) and "@" + profile in v.get("password")))]
            nodes.extend(layer2)
            subfolders = [k for k,v in self.connections[f].items() if isinstance(v, dict) and v.get("type") == "subfolder"]
            for s in subfolders:
                layer3 = [k + "@" + s + "@" + f for k,v in self.connections[f][s].items() if isinstance(v, dict) and v.get("type") == "connection" and ("@" + profile in v.values() or ( isinstance(v.get("password"),list) and "@" + profile in v.get("password")))]
                nodes.extend(layer3)
        return nodes

    @MethodHook
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
            keyfile = self.key
        with open(keyfile) as f:
            key = RSA.import_key(f.read())
            f.close()
        publickey = key.publickey()
        encryptor = PKCS1_OAEP.new(publickey)
        password = encryptor.encrypt(password.encode("utf-8"))
        return str(password)

