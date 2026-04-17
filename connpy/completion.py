import sys
import os

def load_txt_cache(filepath):
    try:
        with open(filepath, "r") as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []

def get_cwd(words, option=None, folderonly=False):
    import glob
    # Expand tilde to home directory if present
    if words[-1].startswith("~"):
        words[-1] = os.path.expanduser(words[-1])
    
    # If option is not provided, try to infer it from the first word
    if option is None and words:
        option = words[0]

    if words[-1] == option:
        path = './*'
    else:
        path = words[-1] + "*"

    pathstrings = glob.glob(path)
    for i in range(len(pathstrings)):
        if os.path.isdir(pathstrings[i]):
            pathstrings[i] += '/'
    pathstrings = [s[2:] if s.startswith('./') else s for s in pathstrings]
    if folderonly:
        pathstrings = [s for s in pathstrings if os.path.isdir(s)]
    return pathstrings

def _get_plugins(which, defaultdir):
    # Path to core_plugins relative to this script
    core_path = os.path.dirname(os.path.realpath(__file__)) + "/core_plugins"
    remote_path = os.path.join(defaultdir, "remote_plugins")

    # Load preferences
    import json
    pref_path = os.path.join(defaultdir, "plugin_preferences.json")
    try:
        with open(pref_path) as f:
            preferences = json.load(f)
    except Exception:
        preferences = {}

    # Load service mode
    # We try to infer if we are in remote mode by checking config.yaml or .folder
    # but for completion usually we just want to know if remote cache exists.
    # However, to be strict we should check preferences.

    def get_plugins_from_directory(directory):
        enabled_files = []
        disabled_files = []
        all_plugins = {}
        # Iterate over all files in the specified folder
        if os.path.exists(directory):
            for file in os.listdir(directory):
                # Check if the file is a Python file
                if file.endswith('.py'):
                    name = os.path.splitext(file)[0]
                    enabled_files.append(name)
                    all_plugins[name] = os.path.join(directory, file)
                # Check if the file is a Python backup file
                elif file.endswith('.py.bkp'):
                    name = os.path.splitext(os.path.splitext(file)[0])[0]
                    disabled_files.append(name)
        return enabled_files, disabled_files, all_plugins

    # Get plugins from all directories
    user_enabled, user_disabled, user_all_plugins = get_plugins_from_directory(defaultdir + "/plugins")
    core_enabled, core_disabled, core_all_plugins = get_plugins_from_directory(core_path)
    remote_enabled, remote_disabled, remote_all_plugins = get_plugins_from_directory(remote_path)

    # Calculate final paths respecting priorities and preferences
    # Priority: User Local > Core Local > Remote (unless preferred)
    
    # Start with core
    final_all_plugins = core_all_plugins.copy()
    # Override with user local
    final_all_plugins.update(user_all_plugins)
    
    # For remote, we only use them if:
    # 1. They don't exist locally OR
    # 2. Preference is explicitly 'remote'
    for name, path in remote_all_plugins.items():
        if name not in final_all_plugins or preferences.get(name) == "remote":
            final_all_plugins[name] = path

    # Combine enabled/disabled for the helper commands
    enabled_files = list(set(user_enabled + core_enabled + [k for k,v in remote_all_plugins.items() if preferences.get(k) == "remote"]))
    disabled_files = list(set(user_disabled + core_disabled))

    # Return based on the command
    if which == "--disable":
        return enabled_files
    elif which == "--enable":
        return disabled_files
    elif which in ["--del", "--update"]:
        all_files = enabled_files + disabled_files
        return all_files
    elif which == "all":
        return final_all_plugins


def _build_tree(nodes, folders, profiles, plugins, configdir):
    """Build the declarative CLI navigation tree.

    Structure:
    - dict:     keys are completions + subnavigation.
                 "__extra__" adds dynamic data.
                 "__exclude_used__" filters already-typed words.
                 "*" absorbs unknown positional words and loops to a specific node.
    - list:     static choice completions.
    - callable: dynamic completions (called with `words`, returns list).
    - None:     no further completions.
    """
    _nodes = lambda w=None: list(nodes)
    _folders = lambda w=None: list(folders)
    _profiles = lambda w=None: list(profiles)
    _nodes_folders = lambda w=None: list(nodes) + list(folders)

    _profile_values = {"__extra__": _profiles}

    # --- Stateful/Looping Nodes ---
    
    # list nodes
    list_nodes = {"__exclude_used__": True}
    list_nodes.update({
        "--format": {"*": list_nodes},
        "--filter": {"*": list_nodes},
        "*": list_nodes
    })

    # export / import / run loops
    export_dict = {"--help": None, "-h": None}
    export_dict.update({
        "*": export_dict,
        "__extra__": lambda w: get_cwd(w, "export", True) + [f for f in folders if not any(x in f for x in w[1:-1])]
    })
    
    import_dict = {"--help": None, "-h": None}
    import_dict.update({
        "*": import_dict,
        "__extra__": lambda w: get_cwd(w, "import")
    })

    run_dict = {"--generate": None, "--help": None, "-g": None, "-h": None}
    run_dict.update({
        "*": run_dict,
        "__extra__": lambda w: get_cwd(w, "run") + list(nodes)
    })

    # State Machine Definitions
    ai_dict = {"__exclude_used__": True, "--help": None, "-h": None}
    for opt in ["--engineer-model", "--engineer-api-key", "--architect-model", "--architect-api-key"]:
        ai_dict[opt] = {"*": ai_dict} # takes value, loops back
    for opt in ["--debug", "--trust", "--list", "--list-sessions", "--session", "--resume", "--delete", "--delete-session", "-y"]:
        ai_dict[opt] = ai_dict # takes no value, loops back
    ai_dict["*"] = ai_dict

    mv_state = {"__extra__": _nodes, "--help": None, "-h": None}
    cp_state = {"__extra__": _nodes, "--help": None, "-h": None}
    ls_state = {
        "profiles": None,
        "nodes": list_nodes,
        "folders": None,
    }

    # --- Main Tree ---
    return {
        "__extra__": lambda w: list(nodes) + list(folders) + (list(plugins.keys()) if plugins else []),

        "--add": {"profile": _profile_values},
        "--del": {"profile": _profile_values, "__extra__": _nodes_folders},
        "--rm":  {"profile": _profile_values, "__extra__": _nodes_folders},
        "--edit": {"profile": _profile_values, "__extra__": _nodes},
        "--mod":  {"profile": _profile_values, "__extra__": _nodes},
        "--show": {"profile": _profile_values, "__extra__": _nodes},
        "--help": None,

        "-a": {"profile": _profile_values},
        "-r": {"profile": _profile_values, "__extra__": _nodes_folders},
        "-e": {"profile": _profile_values, "__extra__": _nodes},
        "-s": {"profile": _profile_values, "__extra__": _nodes},

        "profile": {
            "--add": None, "--rm": _profiles, "--del": _profiles,
            "--edit": _profiles, "--mod": _profiles, "--show": _profiles,
            "--help": None,
            "-a": None, "-r": _profiles, "-e": _profiles, "-s": _profiles, "-h": None,
        },
        "move": mv_state,
        "mv":   mv_state,
        "copy": cp_state,
        "cp":   cp_state,
        
        "list": ls_state,
        "ls":   ls_state,
        
        "bulk": {"--file": None, "--help": None, "-f": None, "-h": None},
        "run": run_dict,
        "export": export_dict,
        "import": import_dict,
        "ai": ai_dict,
        
        "api": {
            "--start": None, "--restart": None, "--stop": None, "--debug": None,
            "--help": None,
            "-s": None, "-r": None, "-x": None, "-d": None, "-h": None,
        },
        "context": {
            "--add": None, "--rm": None, "--del": None,
            "--ls": None, "--set": None,
            "--show": None, "--edit": None, "--mod": None,
            "--help": None,
            "-a": None, "-r": None, "-s": None, "-e": None, "-h": None,
        },
        "plugin": {
            "--add": lambda w: get_cwd(w, "--add"), 
            "--update": lambda w: get_cwd(w, "--update"),
            "--del":     lambda w: _get_plugins("--del", configdir),
            "--enable":  lambda w: _get_plugins("--enable", configdir),
            "--disable": lambda w: _get_plugins("--disable", configdir),
            "--list": None, "--help": None,
            "-h": None,
        },
        "config": {
            "--allow-uppercase": ["true", "false"],
            "--fzf": ["true", "false"],
            "--keepalive": None,
            "--completion": ["bash", "zsh"],
            "--fzf-wrapper": ["bash", "zsh"],
            "--configfolder": lambda w: get_cwd(w, "--configfolder", True),
            "--engineer-model": None, "--engineer-api-key": None,
            "--architect-model": None, "--architect-api-key": None,
            "--theme": None,
            "--service-mode": ["local", "remote"],
            "--remote": None,
            "--sync-remote": ["true", "false"],
            "--trusted-commands": None,
            "--help": None, "-h": None,
        },
        "sync": {
            "--login": None, "--logout": None,
            "--status": None, "--list": None,
            "--once": None, "--restore": None,
            "--start": None, "--stop": None,
            "--id": None, "--nodes": None, "--config": None,
            "--help": None, "-h": None,
        },
    }


def resolve_completion(words, tree):
    """Navigate the tree following typed words, properly handling dynamic state loops."""
    current = tree
    for word in words[:-1]:
        if isinstance(current, dict):
            if word in current:
                current = current[word]
            elif "*" in current:
                current = current["*"]
            else:
                return []
        else:
            return []

    results = []
    if isinstance(current, dict):
        results = [k for k in current 
                   if not k.startswith("__") 
                   and not k.startswith("*") 
                   and not (len(k) == 2 and k in ["mv", "cp", "ls"])
                   and not (len(k) == 2 and k[0] == "-" and k[1] != "-")]
        
        if current.get("__exclude_used__"):
             results = [r for r in results if r not in words[:-1]]
             
        extra = current.get("__extra__")
        if callable(extra):
            results.extend(extra(words))
        elif isinstance(extra, list):
            results.extend(extra)
    elif isinstance(current, list):
        results = list(current)
    elif callable(current):
        results = list(current(words))

    return results


def main():
    home = os.path.expanduser("~")
    defaultdir = home + '/.config/conn'
    pathfile = defaultdir + '/.folder'
    try:
        with open(pathfile, "r") as f:
            configdir = f.read().strip()
    except (FileNotFoundError, IOError):
        configdir = defaultdir
    cachefile = configdir + '/.config.cache.json'
    
    nodes = load_txt_cache(configdir + '/.fzf_nodes_cache.txt')
    folders = load_txt_cache(configdir + '/.folders_cache.txt')
    profiles = load_txt_cache(configdir + '/.profiles_cache.txt')
    plugins = _get_plugins("all", configdir)
    
    info = {}
    info["config"] = None
    info["nodes"] = nodes
    info["folders"] = folders
    info["profiles"] = profiles
    info["plugins"] = plugins
    app = sys.argv[1]
    if app in ["bash", "zsh"]:
        positions = [2,4]
    else:
        positions = [1,3]
    wordsnumber = int(sys.argv[positions[0]])
    words = sys.argv[positions[1]:]

    # --- Plugin completion ---
    # Try new tree API first: _connpy_tree integrates into the main tree.
    # Fall back to legacy _connpy_completion for older plugins.
    if wordsnumber >= 3 and plugins and words[0] in plugins:
        import importlib.util
        plugin_path = plugins[words[0]]
        try:
            spec = importlib.util.spec_from_file_location("module.name", plugin_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.get_cwd = get_cwd
        except Exception:
            exit()

        # New API: _connpy_tree → integrate into main tree and use resolver
        if hasattr(module, "_connpy_tree"):
            plugin_node = module._connpy_tree(info)
            tree = _build_tree(nodes, folders, profiles, plugins, configdir)
            tree[words[0]] = plugin_node
            strings = resolve_completion(words, tree)

        # Legacy API: _connpy_completion → delegate entirely
        elif hasattr(module, "_connpy_completion"):
            import json
            try:
                with open(cachefile, "r") as jsonconf:
                    info["config"] = json.load(jsonconf)
            except Exception:
                try:
                    import yaml
                    with open(configdir + '/config.yaml', "r") as yamlconf:
                        info["config"] = yaml.safe_load(yamlconf)
                except Exception:
                    info["config"] = {}
            try:
                plugin_completion = getattr(module, "_connpy_completion")
                strings = plugin_completion(wordsnumber, words, info)
            except Exception:
                exit()
        else:
            exit()

    # --- Tree-based completion ---
    else:
        tree = _build_tree(nodes, folders, profiles, plugins, configdir)
        strings = resolve_completion(words, tree)

    current_word = words[-1] if len(words) > 0 else ""
    matches = [s for s in strings if s.startswith(current_word)]

    if app == "bash":
        strings = [s if s.endswith('/') else f"'{s} '" for s in matches]
    else:
        strings = matches
        
    print('\t'.join(strings))

if __name__ == '__main__':
    sys.exit(main())
