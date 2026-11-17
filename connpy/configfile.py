#!/usr/bin/env python3
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from contextlib import suppress
from copy import deepcopy
from pathlib import Path

import yaml

from . import printer
from .auth_utils import (
    SanitizedAuthError,
    extract_profile_refs_from_value,
    is_compose_expression,
    resolve_fallback_entries,
    validate_auth_budget_tags,
    validate_compose_syntax,
    validate_config_auth_settings,
    validate_fallback_entries,
)
from .hooks import ClassHook, MethodHook


class NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True

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

    def __init__(self, conf = None, key = None, shared_config = None):
        self._mutation_lock = threading.RLock()
        self._cache_dirty = False
        self._shared_config = shared_config
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
                            yaml.dump(old_data, f, Dumper=NoAliasDumper, default_flow_style=False, sort_keys=False)
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
                except Exception as e:  # noqa: BLE001
                    if os.path.exists(self.file):
                        with suppress(OSError):
                            os.remove(self.file)
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
        
        self._privatekey_obj = None
        self._publickey_obj = None
        if not os.path.exists(self.key):
            self._createkey(self.key)

        # Self-heal text caches if they are missing
        if not os.path.exists(self.fzf_cachefile) or not os.path.exists(self.folders_cachefile) or not os.path.exists(self.profiles_cachefile):
            self._generate_nodes_cache()

    @property
    def privatekey(self):
        if getattr(self, '_privatekey_obj', None) is None:
            from Crypto.PublicKey import RSA
            if not os.path.exists(self.key):
                self._createkey(self.key)
            with open(self.key) as f:
                self._privatekey_obj = RSA.import_key(f.read())
        return self._privatekey_obj

    @privatekey.setter
    def privatekey(self, value):
        self._privatekey_obj = value

    @property
    def publickey(self):
        if getattr(self, '_publickey_obj', None) is None:
            self._publickey_obj = self.privatekey.publickey()
        return self._publickey_obj

    @publickey.setter
    def publickey(self, value):
        self._publickey_obj = value


    def get_effective_setting(self, key, default=None):
        """Get config setting with shared fallback for inheritable keys."""
        val = self.config.get(key)
        if key == "ai":
            if val is not None:
                if self._shared_config:
                    import copy
                    # Deep merge: shared as base, user overrides
                    base = copy.deepcopy(self._shared_config.config.get(key, {}))
                    if isinstance(base, dict) and isinstance(val, dict):
                        # Credential isolation:
                        # If user defines engineer credentials, discard shared ones
                        if "engineer_api_key" in val or "engineer_auth" in val:
                            base.pop("engineer_api_key", None)
                            base.pop("engineer_auth", None)
                        # If user defines architect credentials, discard shared ones
                        if "architect_api_key" in val or "architect_auth" in val:
                            base.pop("architect_api_key", None)
                            base.pop("architect_auth", None)
                            
                        # Recursive update for inner dictionaries (like mcp_servers or model details)
                        def deep_merge(d1, d2):
                            for k, v in d2.items():
                                if isinstance(v, dict) and k in d1 and isinstance(d1[k], dict):
                                    deep_merge(d1[k], v)
                                else:
                                    d1[k] = copy.deepcopy(v)
                        deep_merge(base, val)
                        return base
                return val
            elif self._shared_config:
                return self._shared_config.config.get(key, default)
        
        return val if val is not None else default


    def _validate_config_entry_unlocked(self, entry):
        """Strictly validate tags, fallback, and password compose syntax on a node or profile entry."""
        if not isinstance(entry, dict):
            return
        if "tags" in entry and entry["tags"] not in ("", None):
            validate_auth_budget_tags(entry["tags"])
        if "fallback" in entry and entry["fallback"] not in (None, "", []):
            validate_fallback_entries(entry["fallback"])
        pw = entry.get("password")
        if is_compose_expression(pw):
            validate_compose_syntax(pw)
        elif isinstance(pw, (list, tuple)):
            for item in pw:
                if is_compose_expression(item):
                    validate_compose_syntax(item)

    def _walk_connections_validate_unlocked(self, container):
        """Recursively validate all connection dicts inside nested folders/subfolders."""
        if not isinstance(container, dict):
            raise TypeError("Connections container must be a dict")
        for key, val in container.items():
            if key == "type" or not isinstance(val, dict):
                continue
            entry_type = val.get("type")
            if entry_type in ("folder", "subfolder"):
                self._walk_connections_validate_unlocked(val)
            else:
                self._validate_config_entry_unlocked(val)

    def _validate_config_strict(self, data):
        """Strictly validate config structure, budgets, and fallback rules; raises on error."""
        if not isinstance(data, dict):
            raise TypeError("Config root must be a dict")
        required = {"config", "connections", "profiles"}
        if not required.issubset(data.keys()):
            raise ValueError("Config missing required sections")
        if not isinstance(data["config"], dict):
            raise TypeError("'config' section must be a dict")
        if not isinstance(data["connections"], dict):
            raise TypeError("'connections' section must be a dict")
        if not isinstance(data["profiles"], dict):
            raise TypeError("'profiles' section must be a dict")
        validate_config_auth_settings(data["config"])
        self._walk_connections_validate_unlocked(data["connections"])
        for prof_val in data["profiles"].values():
            if not isinstance(prof_val, dict):
                raise TypeError("Each profile entry must be a dict")
            self._validate_config_entry_unlocked(prof_val)

    def _validate_config(self, data):
        """Verify config data has the required structure."""
        try:
            self._validate_config_strict(data)
        except (ValueError, TypeError, SanitizedAuthError):
            return False
        return True

    def _atomic_write_text_unlocked(self, target_path, writer):
        """Atomically write text file with symlink refusal, 0o600 mode, fsync, and os.replace."""
        if os.path.islink(target_path):
            raise OSError(f"Refusing to write through symlink: {target_path}")
        target_dir = os.path.dirname(os.path.abspath(target_path)) or "."
        os.makedirs(target_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=target_dir,
            prefix=f".{os.path.basename(target_path)}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                writer(f)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, target_path)
        except BaseException:
            with suppress(OSError):
                os.unlink(tmp_path)
            raise

    def _fsync_dir_nonfatal(self, file_path):
        target_dir = os.path.dirname(os.path.abspath(file_path)) or "."
        try:
            dir_fd = os.open(target_dir, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        except OSError:
            return
        finally:
            with suppress(OSError):
                os.close(dir_fd)

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
                        with suppress(OSError):
                            self._atomic_write_text_unlocked(
                                conf,
                                lambda fp: yaml.dump(
                                    data,
                                    fp,
                                    Dumper=NoAliasDumper,
                                    default_flow_style=False,
                                    sort_keys=False,
                                ),
                            )
                        return data
                # Both broken or no cache - create fresh
                printer.error("Config file is corrupt and no valid cache exists. Creating default config.")
                return self._createconfig(conf)
            with suppress(OSError, TypeError, ValueError):
                self._atomic_write_text_unlocked(self.cachefile, lambda fp: json.dump(data, fp))
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
        defaultconfig = {'config': {'case': False, 'idletime': 30, 'fzf': False}, 'connections': {}, 'profiles': { "default": { "host":"", "protocol":"ssh", "port":"", "user":"", "password":"", "options":"", "logs":"", "tags": "", "jumphost":"", "fallback": ""}}}
        self._atomic_write_text_unlocked(
            conf,
            lambda f: yaml.dump(defaultconfig, f, Dumper=NoAliasDumper, default_flow_style=False, sort_keys=False),
        )
        with suppress(OSError, TypeError, ValueError):
            self._atomic_write_text_unlocked(self.cachefile, lambda f: json.dump(defaultconfig, f))
        return defaultconfig

    def _saveconfig_unlocked(self, conf, *, _raise_on_error=False):
        # Save config file atomically inside _mutation_lock without invoking hooks or printer
        newconfig = {
            "config": self.config,
            "connections": self.connections,
            "profiles": self.profiles,
        }
        try:
            self._atomic_write_text_unlocked(
                conf,
                lambda f: yaml.dump(
                    newconfig,
                    f,
                    Dumper=NoAliasDumper,
                    default_flow_style=False,
                    sort_keys=False,
                ),
            )
        except (OSError, TypeError, yaml.YAMLError) as e:
            if _raise_on_error:
                raise
            return 1, f"Failed to save config: {e}"

        # YAML is safely committed on disk; fsync dir and sidecar caches are non-fatal
        self._fsync_dir_nonfatal(conf)
        try:
            self._atomic_write_text_unlocked(self.cachefile, lambda f: json.dump(newconfig, f))
            self._generate_nodes_cache_unlocked(_raise_on_error=True)
            self._cache_dirty = False
        except (OSError, TypeError, ValueError):
            self._cache_dirty = True
        return 0, None

    def _run_saveconfig_post_hooks(self, conf, result):
        """Run @MethodHook post-hooks registered for _saveconfig outside _mutation_lock."""
        cls_desc = type(self).__dict__.get("_saveconfig")
        cls_post = list(cls_desc.post_hooks) if isinstance(cls_desc, MethodHook) else []
        inst_post = (
            self.__dict__.get("_method_post_hooks", {}).get("_saveconfig", [])
            if isinstance(self.__dict__.get("_method_post_hooks"), dict)
            else []
        )
        all_post = cls_post + [h for h in inst_post if h not in cls_post]
        for hook in all_post:
            try:
                result = hook(self, conf, result=result)
            except Exception as e:  # noqa: BLE001
                hook_name = getattr(hook, "__name__", str(hook))
                printer.error(f"_saveconfig Post-hook {hook_name} raised an exception: {e}")
        return result

    @MethodHook
    def _saveconfig(self, conf):
        #Save config file atomically to prevent corruption
        with self._mutation_lock:
            rc, err_msg = self._saveconfig_unlocked(conf, _raise_on_error=False)
        if err_msg:
            printer.error(err_msg)
        return rc

    def transactional_update(self, mutator):
        """Run mutator atomically inside _mutation_lock with in-place RAM rollback on error."""
        save_desc = type(self).__dict__.get("_saveconfig")
        mocked_save = "_saveconfig" in self.__dict__ or not isinstance(save_desc, MethodHook)
        with self._mutation_lock:
            snap_config = deepcopy(self.config)
            snap_connections = deepcopy(self.connections)
            snap_profiles = deepcopy(self.profiles)
            try:
                result = mutator(self)
                self._validate_config_strict(
                    {
                        "config": self.config,
                        "connections": self.connections,
                        "profiles": self.profiles,
                    }
                )
                if mocked_save:
                    rc = self._saveconfig(self.file)
                    if rc != 0:
                        raise OSError("Failed to save config")
                else:
                    self._saveconfig_unlocked(self.file, _raise_on_error=True)
            except BaseException:
                self.config.clear()
                self.config.update(snap_config)
                self.connections.clear()
                self.connections.update(snap_connections)
                self.profiles.clear()
                self.profiles.update(snap_profiles)
                raise
        if not mocked_save:
            self._run_saveconfig_post_hooks(self.file, 0)
        return result

    def refresh_cache(self):
        """Rebuild JSON and fzf sidecar caches atomically; returns True on success."""
        with self._mutation_lock:
            snapshot = {
                "config": self.config,
                "connections": self.connections,
                "profiles": self.profiles,
            }
            try:
                self._atomic_write_text_unlocked(self.cachefile, lambda f: json.dump(snapshot, f))
                self._generate_nodes_cache_unlocked(_raise_on_error=True)
                self._cache_dirty = False
                return True
            except (OSError, TypeError, ValueError):
                self._cache_dirty = True
                return False

    def _generate_nodes_cache_unlocked(self, nodes=None, folders=None, profiles=None, *, _raise_on_error=False):
        try:
            if nodes is None:
                nodes = self._getallnodes_unlocked()
            if folders is None:
                folders = self._getallfolders_unlocked()
            if profiles is None:
                profiles = list(self.profiles.keys())

            self._atomic_write_text_unlocked(self.fzf_cachefile, lambda f: f.write("\n".join(nodes)))
            self._atomic_write_text_unlocked(self.folders_cachefile, lambda f: f.write("\n".join(folders)))
            self._atomic_write_text_unlocked(self.profiles_cachefile, lambda f: f.write("\n".join(profiles)))
        except (OSError, TypeError, ValueError):
            if _raise_on_error:
                raise
            self._cache_dirty = True

    def _generate_nodes_cache(self, nodes=None, folders=None, profiles=None):
        with self._mutation_lock:
            self._generate_nodes_cache_unlocked(
                nodes=nodes,
                folders=folders,
                profiles=profiles,
                _raise_on_error=False,
            )


    def _createkey(self, keyfile):
        #Create key file
        from Crypto.PublicKey import RSA
        key = RSA.generate(2048)
        with open(keyfile,'wb') as f:
            f.write(key.export_key('PEM'))
            f.close()
            os.chmod(keyfile, 0o600)
        return key

    def _explode_unique_unlocked(self, unique):
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
    def _explode_unique(self, unique):
        return self._explode_unique_unlocked(unique)

    def _resolve_profile_attr_unlocked(self, prof_name, key, *, depth=0, visited=frozenset()):
        if prof_name in visited or depth >= 5:
            return None
        prof = self.profiles.get(prof_name)
        if not isinstance(prof, dict) or key not in prof:
            return None
        val = prof[key]
        if key == "password":
            if isinstance(val, str):
                if is_compose_expression(val):
                    return val
                if val.startswith("@") and val[1:] in self.profiles:
                    sub = self._resolve_profile_attr_unlocked(
                        val[1:],
                        key,
                        depth=depth + 1,
                        visited=visited | {prof_name},
                    )
                    return sub if sub is not None else val
                return val
            if isinstance(val, list):
                resolved_list = []
                for item in val:
                    if (
                        isinstance(item, str)
                        and not is_compose_expression(item)
                        and item.startswith("@")
                        and item[1:] in self.profiles
                    ):
                        sub = self._resolve_profile_attr_unlocked(
                            item[1:],
                            "password",
                            depth=depth + 1,
                            visited=visited | {prof_name},
                        )
                        if isinstance(sub, list):
                            resolved_list.extend(deepcopy(sub))
                        elif sub is not None:
                            resolved_list.append(deepcopy(sub))
                        else:
                            resolved_list.append(item)
                    else:
                        resolved_list.append(deepcopy(item))
                return resolved_list
            return deepcopy(val)

        if isinstance(val, str) and val.startswith("@") and val[1:] in self.profiles:
            sub = self._resolve_profile_attr_unlocked(
                val[1:],
                key,
                depth=depth + 1,
                visited=visited | {prof_name},
            )
            return sub if sub is not None else ""
        return deepcopy(val)

    def _extract_node_fields_unlocked(self, node_dict):
        """Resolve @profile references on a copied node dict, keeping legacy core.node(**cfg) contract."""
        raw_fb = node_dict.pop("fallback", None)

        for key, value in list(node_dict.items()):
            if key == "password":
                if isinstance(value, str):
                    if is_compose_expression(value):
                        continue
                    if value.startswith("@") and value[1:] in self.profiles:
                        resolved = self._resolve_profile_attr_unlocked(value[1:], "password")
                        node_dict["password"] = resolved if resolved is not None else ""
                elif isinstance(value, list):
                    resolved_pw = []
                    for item in value:
                        if (
                            isinstance(item, str)
                            and not is_compose_expression(item)
                            and item.startswith("@")
                            and item[1:] in self.profiles
                        ):
                            sub = self._resolve_profile_attr_unlocked(item[1:], "password")
                            if isinstance(sub, list):
                                resolved_pw.extend(deepcopy(sub))
                            elif sub is not None:
                                resolved_pw.append(deepcopy(sub))
                            else:
                                resolved_pw.append("")
                        else:
                            resolved_pw.append(deepcopy(item))
                    node_dict["password"] = resolved_pw
                continue

            if isinstance(value, str) and value.startswith("@"):
                ref_name = value[1:]
                if ref_name in self.profiles:
                    res = self._resolve_profile_attr_unlocked(ref_name, key)
                    node_dict[key] = res if res is not None else ""
                else:
                    node_dict[key] = ""
            elif value == "" and key == "protocol":
                default_proto = self.profiles.get("default", {}).get("protocol", "ssh")
                node_dict[key] = default_proto if default_proto else "ssh"

        resolved_fb = []
        if raw_fb not in (None, "", []):
            resolved_fb = resolve_fallback_entries(raw_fb, self.profiles)

        if resolved_fb:
            tags_val = node_dict.get("tags", "")
            if isinstance(tags_val, dict):
                tags_copy = deepcopy(tags_val)
                tags_copy["_fallback"] = resolved_fb
                node_dict["tags"] = tags_copy
            elif tags_val in ("", None):
                node_dict["tags"] = {"_fallback": resolved_fb}
            else:
                node_dict["tags"] = {"_raw": tags_val, "_fallback": resolved_fb}
        elif isinstance(node_dict.get("tags"), dict) and node_dict["tags"].get("fallback") not in (None, "", []):
            tags_copy = deepcopy(node_dict["tags"])
            tags_copy["_fallback"] = resolve_fallback_entries(tags_copy["fallback"], self.profiles)
            node_dict["tags"] = tags_copy

    def getitem_unlocked(self, unique, keys=None, extract=False):
        uniques = self._explode_unique_unlocked(unique)
        if unique.startswith("@"):
            if uniques.keys() >= {"folder", "subfolder"}:
                folder = self.connections[uniques["folder"]][uniques["subfolder"]]
            else:
                folder = self.connections[uniques["folder"]]
            newfolder = deepcopy(folder)
            newfolder.pop("type")
            for node_name in list(folder.keys()):
                if node_name == "type":
                    continue
                if "type" in newfolder[node_name]:
                    if newfolder[node_name]["type"] == "subfolder":
                        newfolder.pop(node_name)
                    else:
                        newfolder[node_name].pop("type")

            if keys is not None:
                newfolder = {k: newfolder[k] for k in keys}

            if extract:
                for node_keys in newfolder.values():
                    self._extract_node_fields_unlocked(node_keys)

            return {f"{k}{unique}": v for k, v in newfolder.items()}

        if uniques.keys() >= {"folder", "subfolder"}:
            node = self.connections[uniques["folder"]][uniques["subfolder"]][uniques["id"]]
        elif "folder" in uniques:
            node = self.connections[uniques["folder"]][uniques["id"]]
        else:
            node = self.connections[uniques["id"]]
        newnode = deepcopy(node)
        newnode.pop("type")
        if extract:
            self._extract_node_fields_unlocked(newnode)
        return newnode

    @MethodHook
    def getitem(self, unique, keys = None, extract = False):
        """Get a node or folder of nodes from configfile."""
        with self._mutation_lock:
            return self.getitem_unlocked(unique, keys=keys, extract=extract)

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
        if isinstance(uniques, str):
            uniques = [uniques]
        with self._mutation_lock:
            nodes = {}
            case_sensitive = self.config.get("case", False)
            for item in uniques:
                target = item if case_sensitive else item.lower()
                if target.startswith("@"):
                    nodes.update(self.getitem_unlocked(target, extract=extract))
                else:
                    nodes[target] = self.getitem_unlocked(target, extract=extract)
            return nodes

    def _connections_add_unlocked(
        self,
        *,
        id,
        host,
        folder='',
        subfolder='',
        options='',
        logs='',
        password='',
        port='',
        protocol='',
        user='',
        tags='',
        jumphost='',
        fallback='',
        type="connection",
    ):
        entry = {
            "host": host,
            "options": options,
            "logs": logs,
            "password": password,
            "port": port,
            "protocol": protocol,
            "user": user,
            "tags": tags,
            "jumphost": jumphost,
            "fallback": fallback,
            "type": type,
        }
        if folder == '':
            self.connections[id] = entry
        elif subfolder == '':
            self.connections[folder][id] = entry
        else:
            self.connections[folder][subfolder][id] = entry

    @MethodHook
    def _connections_add(self,*, id, host, folder='', subfolder='', options='', logs='', password='', port='', protocol='', user='', tags='', jumphost='', fallback='', type = "connection" ):
        #Add connection from config
        with self._mutation_lock:
            self._connections_add_unlocked(
                id=id,
                host=host,
                folder=folder,
                subfolder=subfolder,
                options=options,
                logs=logs,
                password=password,
                port=port,
                protocol=protocol,
                user=user,
                tags=tags,
                jumphost=jumphost,
                fallback=fallback,
                type=type,
            )
            

    def _connections_del_unlocked(self, *, id, folder='', subfolder=''):
        if folder == '':
            del self.connections[id]
        elif subfolder == '':
            del self.connections[folder][id]
        else:
            del self.connections[folder][subfolder][id]

    @MethodHook
    def _connections_del(self,*, id, folder='', subfolder=''):
        #Delete connection from config
        with self._mutation_lock:
            self._connections_del_unlocked(id=id, folder=folder, subfolder=subfolder)

    def _folder_add_unlocked(self, *, folder, subfolder=''):
        if subfolder == '':
            if folder not in self.connections:
                self.connections[folder] = {"type": "folder"}
        else:
            if subfolder not in self.connections[folder]:
                self.connections[folder][subfolder] = {"type": "subfolder"}

    @MethodHook
    def _folder_add(self,*, folder, subfolder = ''):
        #Add Folder from config
        with self._mutation_lock:
            self._folder_add_unlocked(folder=folder, subfolder=subfolder)

    def _folder_del_unlocked(self, *, folder, subfolder=''):
        if subfolder == '':
            del self.connections[folder]
        else:
            del self.connections[folder][subfolder]

    @MethodHook
    def _folder_del(self,*, folder, subfolder=''):
        #Delete folder from config
        with self._mutation_lock:
            self._folder_del_unlocked(folder=folder, subfolder=subfolder)

    def _profiles_add_unlocked(self, *, id, host='', options='', logs='', password='', port='', protocol='', user='', tags='', jumphost='', fallback=''):
        self.profiles[id] = {
            "host": host,
            "options": options,
            "logs": logs,
            "password": password,
            "port": port,
            "protocol": protocol,
            "user": user,
            "tags": tags,
            "jumphost": jumphost,
            "fallback": fallback,
        }

    @MethodHook
    def _profiles_add(self,*, id, host = '', options='', logs='', password='', port='', protocol='', user='', tags='', jumphost='', fallback=''):
        #Add profile from config
        with self._mutation_lock:
            self._profiles_add_unlocked(
                id=id,
                host=host,
                options=options,
                logs=logs,
                password=password,
                port=port,
                protocol=protocol,
                user=user,
                tags=tags,
                jumphost=jumphost,
                fallback=fallback,
            )

    def _profiles_del_unlocked(self, *, id):
        del self.profiles[id]

    @MethodHook
    def _profiles_del(self,*, id ):
        #Delete profile from config
        with self._mutation_lock:
            self._profiles_del_unlocked(id=id)

    @staticmethod
    def _normalize_filter_arg(filter_arg):
        if not filter_arg:
            return []
        if isinstance(filter_arg, str):
            return [filter_arg]
        if isinstance(filter_arg, list):
            return [item for item in filter_arg if isinstance(item, str)]
        return None

    def _getallnodes_unlocked(self, filter=None):
        nodes = []
        layer1 = [k for k, v in self.connections.items() if isinstance(v, dict) and v.get("type") == "connection"]
        folders = [k for k, v in self.connections.items() if isinstance(v, dict) and v.get("type") == "folder"]
        nodes.extend(layer1)
        for f in folders:
            layer2 = [k + "@" + f for k, v in self.connections[f].items() if isinstance(v, dict) and v.get("type") == "connection"]
            nodes.extend(layer2)
            subfolders = [k for k, v in self.connections[f].items() if isinstance(v, dict) and v.get("type") == "subfolder"]
            for s in subfolders:
                layer3 = [k + "@" + s + "@" + f for k, v in self.connections[f][s].items() if isinstance(v, dict) and v.get("type") == "connection"]
                nodes.extend(layer3)
        if filter:
            flat_filter = self._normalize_filter_arg(filter)
            flags = re.IGNORECASE if not self.config.get("case", False) else 0
            nodes = [item for item in nodes if any(re.search(pattern, item, flags) for pattern in flat_filter)]
        return nodes

    @MethodHook
    def _getallnodes(self, filter = None):
        #get all nodes on configfile
        if filter and self._normalize_filter_arg(filter) is None:
            printer.error("Filter must be a string or a list of strings")
            sys.exit(1)
        with self._mutation_lock:
            return self._getallnodes_unlocked(filter=filter)

    def _getallnodesfull_unlocked(self, filter=None, extract=True):
        nodes = {}
        layer1 = {k: deepcopy(v) for k, v in self.connections.items() if isinstance(v, dict) and v.get("type") == "connection"}
        folders = [k for k, v in self.connections.items() if isinstance(v, dict) and v.get("type") == "folder"]
        nodes.update(layer1)
        for f in folders:
            layer2 = {k + "@" + f: deepcopy(v) for k, v in self.connections[f].items() if isinstance(v, dict) and v.get("type") == "connection"}
            nodes.update(layer2)
            subfolders = [k for k, v in self.connections[f].items() if isinstance(v, dict) and v.get("type") == "subfolder"]
            for s in subfolders:
                layer3 = {k + "@" + s + "@" + f: deepcopy(v) for k, v in self.connections[f][s].items() if isinstance(v, dict) and v.get("type") == "connection"}
                nodes.update(layer3)
        if filter:
            flat_filter = self._normalize_filter_arg(filter)
            flat_filter = ["^(?!.*@).+$" if item == "@" else item for item in flat_filter]
            nodes = {k: v for k, v in nodes.items() if any(re.search(pattern, k) for pattern in flat_filter)}
        if extract:
            for node_dict in nodes.values():
                self._extract_node_fields_unlocked(node_dict)
        return nodes

    @MethodHook
    def _getallnodesfull(self, filter = None, extract = True):
        #get all nodes on configfile with all their attributes.
        if filter and self._normalize_filter_arg(filter) is None:
            printer.error("Filter must be a string or a list of strings")
            sys.exit(1)
        with self._mutation_lock:
            return self._getallnodesfull_unlocked(filter=filter, extract=extract)

    def _getallfolders_unlocked(self):
        folders = ["@" + k for k, v in self.connections.items() if isinstance(v, dict) and v.get("type") == "folder"]
        subfolders = []
        for f in folders:
            s = ["@" + k + f for k, v in self.connections[f[1:]].items() if isinstance(v, dict) and v.get("type") == "subfolder"]
            subfolders.extend(s)
        folders.extend(subfolders)
        return folders

    @MethodHook
    def _getallfolders(self):
        #get all folders on configfile
        with self._mutation_lock:
            return self._getallfolders_unlocked()

    def _profileused_unlocked(self, profile):
        target_profiles = {profile}
        changed = True
        while changed:
            changed = False
            for pname, pval in self.profiles.items():
                if (
                    pname not in target_profiles
                    and isinstance(pval, dict)
                    and (extract_profile_refs_from_value(pval) & target_profiles)
                ):
                    target_profiles.add(pname)
                    changed = True

        nodes = []
        layer1 = [
            k
            for k, v in self.connections.items()
            if isinstance(v, dict)
            and v.get("type") == "connection"
            and bool(extract_profile_refs_from_value(v) & target_profiles)
        ]
        folders = [k for k, v in self.connections.items() if isinstance(v, dict) and v.get("type") == "folder"]
        nodes.extend(layer1)
        for f in folders:
            layer2 = [
                k + "@" + f
                for k, v in self.connections[f].items()
                if isinstance(v, dict)
                and v.get("type") == "connection"
                and bool(extract_profile_refs_from_value(v) & target_profiles)
            ]
            nodes.extend(layer2)
            subfolders = [k for k, v in self.connections[f].items() if isinstance(v, dict) and v.get("type") == "subfolder"]
            for s in subfolders:
                layer3 = [
                    k + "@" + s + "@" + f
                    for k, v in self.connections[f][s].items()
                    if isinstance(v, dict)
                    and v.get("type") == "connection"
                    and bool(extract_profile_refs_from_value(v) & target_profiles)
                ]
                nodes.extend(layer3)
        return nodes

    @MethodHook
    def _profileused(self, profile):
        #Return all the nodes that uses this profile.
        with self._mutation_lock:
            return self._profileused_unlocked(profile)

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
            from Crypto.PublicKey import RSA
            from Crypto.Cipher import PKCS1_OAEP
            key = RSA.import_key(f.read())
            f.close()
        publickey = key.publickey()
        encryptor = PKCS1_OAEP.new(publickey)
        password = encryptor.encrypt(password.encode("utf-8"))
        return str(password)

