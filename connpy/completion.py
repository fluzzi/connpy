import sys
import os
import json
import glob
import importlib.util

def _getallnodes(config):
    #get all nodes on configfile
    nodes = []
    layer1 = [k for k,v in config["connections"].items() if isinstance(v, dict) and v["type"] == "connection"]
    folders = [k for k,v in config["connections"].items() if isinstance(v, dict) and v["type"] == "folder"]
    nodes.extend(layer1)
    for f in folders:
        layer2 = [k + "@" + f for k,v in config["connections"][f].items() if isinstance(v, dict) and v["type"] == "connection"]
        nodes.extend(layer2)
        subfolders = [k for k,v in config["connections"][f].items() if isinstance(v, dict) and v["type"] == "subfolder"]
        for s in subfolders:
            layer3 = [k + "@" + s + "@" + f for k,v in config["connections"][f][s].items() if isinstance(v, dict) and v["type"] == "connection"]
            nodes.extend(layer3)
    return nodes

def _getallfolders(config):
    #get all folders on configfile
    folders = ["@" + k for k,v in config["connections"].items() if isinstance(v, dict) and v["type"] == "folder"]
    subfolders = []
    for f in folders:
        s = ["@" + k + f for k,v in config["connections"][f[1:]].items() if isinstance(v, dict) and v["type"] == "subfolder"]
        subfolders.extend(s)
    folders.extend(subfolders)
    return folders

def _getcwd(words, option, folderonly=False):
    # Expand tilde to home directory if present
    if words[-1].startswith("~"):
        words[-1] = os.path.expanduser(words[-1])
    
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

    def get_plugins_from_directory(directory):
        enabled_files = []
        disabled_files = []
        all_plugins = {}
        # Iterate over all files in the specified folder
        if os.path.exists(directory):
            for file in os.listdir(directory):
                # Check if the file is a Python file
                if file.endswith('.py'):
                    enabled_files.append(os.path.splitext(file)[0])
                    all_plugins[os.path.splitext(file)[0]] = os.path.join(directory, file)
                # Check if the file is a Python backup file
                elif file.endswith('.py.bkp'):
                    disabled_files.append(os.path.splitext(os.path.splitext(file)[0])[0])
        return enabled_files, disabled_files, all_plugins

    # Get plugins from both directories
    user_enabled, user_disabled, user_all_plugins = get_plugins_from_directory(defaultdir + "/plugins")
    core_enabled, core_disabled, core_all_plugins = get_plugins_from_directory(core_path)

    # Combine the results from user and core plugins
    enabled_files = user_enabled
    disabled_files = user_disabled
    all_plugins = {**user_all_plugins, **core_all_plugins}  # Merge dictionaries

    # Return based on the command
    if which == "--disable":
        return enabled_files
    elif which == "--enable":
        return disabled_files
    elif which in ["--del", "--update"]:
        all_files = enabled_files + disabled_files
        return all_files
    elif which == "all":
        return all_plugins

def main():
    home = os.path.expanduser("~")
    defaultdir = home + '/.config/conn'
    pathfile = defaultdir + '/.folder'
    try:
        with open(pathfile, "r") as f:
            configdir = f.read().strip()
    except:
        configdir = defaultdir
    defaultfile = configdir + '/config.json'
    jsonconf = open(defaultfile)
    config = json.load(jsonconf)
    nodes = _getallnodes(config)
    folders = _getallfolders(config)
    profiles = list(config["profiles"].keys())
    plugins = _get_plugins("all", defaultdir)
    info = {}
    info["config"] = config
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
    if wordsnumber == 2:
        strings=["--add", "--del", "--rm", "--edit", "--mod", "--show", "mv", "move", "ls", "list", "cp", "copy", "profile", "run", "bulk", "config", "api", "ai", "export", "import", "--help", "plugin"]
        if plugins:
            strings.extend(plugins.keys())
        strings.extend(nodes)
        strings.extend(folders)

    elif wordsnumber >=3 and words[0] in plugins.keys():
        try:
            spec = importlib.util.spec_from_file_location("module.name", plugins[words[0]])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            plugin_completion = getattr(module, "_connpy_completion")
            strings = plugin_completion(wordsnumber, words, info)
        except:
            exit()
    elif wordsnumber >= 3 and words[0] == "ai":
        if wordsnumber == 3:
            strings = ["--help", "--org", "--model", "--api_key"]
        else:
            strings = ["--org", "--model", "--api_key"]
    elif wordsnumber == 3:
        strings=[]
        if words[0] == "profile":
            strings=["--add", "--rm", "--del", "--edit", "--mod", "--show", "--help"]
        if words[0] == "config":
            strings=["--allow-uppercase", "--keepalive", "--completion", "--fzf", "--configfolder", "--openai-org", "--openai-org-api-key", "--openai-org-model","--help"]
        if words[0] == "api":
            strings=["--start", "--stop", "--restart", "--debug", "--help"]
        if words[0] in ["--mod", "--edit", "-e", "--show", "-s", "--add", "-a", "--rm", "--del", "-r"]:
            strings=["profile"]
        if words[0] in ["list", "ls"]:
            strings=["profiles", "nodes", "folders"]
        if words[0] in ["bulk", "mv", "cp", "copy"]:
            strings=["--help"]
        if words[0] in ["--rm", "--del", "-r"]:
            strings.extend(folders)
        if words[0] in ["--rm", "--del", "-r", "--mod", "--edit", "-e", "--show", "-s", "mv", "move", "cp", "copy"]:
            strings.extend(nodes)
        if words[0] == "plugin":
            strings = ["--help", "--add", "--update", "--del", "--enable", "--disable", "--list"]
        if words[0] in ["run", "import", "export"]:
            strings = ["--help"]
            if words[0] == "export":
                pathstrings = _getcwd(words, words[0], True)
            else:
                pathstrings = _getcwd(words, words[0])
            strings.extend(pathstrings)
            if words[0] == "run":
                strings.extend(nodes)

    elif wordsnumber >= 4 and words[0] == "export" and words[1] != "--help":
        strings = [item for item in folders if not any(word in item for word in words[:-1])]

    elif wordsnumber >= 4 and words[0] in ["list", "ls"] and words[1] == "nodes":
        options = ["--format", "--filter"]
        strings = [item for item in options if not any(word in item for word in words[:-1])]

    elif wordsnumber == 4:
          strings=[]
          if words[0] == "profile" and words[1] in ["--rm", "--del", "-r", "--mod", "--edit", "-e", "--show", "-s"]:
              strings.extend(profiles)
          if words[1] == "profile" and words[0] in ["--rm", "--del", "-r", "--mod", "--edit", "-e", "--show", "-s"]:
              strings.extend(profiles)
          if words[0] == "config" and words[1] == "--completion":
              strings=["bash", "zsh"]
          if words[0] == "config" and words[1] in ["--fzf", "--allow-uppercase"]:
              strings=["true", "false"]
          if words[0] == "config" and words[1] in ["--configfolder"]:
              strings=_getcwd(words,words[1],True)
          if words[0] == "plugin" and words[1] in ["--update", "--del", "--enable", "--disable"]:
              strings=_get_plugins(words[1], defaultdir)

    elif wordsnumber == 5 and words[0] == "plugin" and words[1] in ["--add", "--update"]:
            strings=_getcwd(words, words[2])
    else:
        exit()


    if app == "bash":
        strings = [s if s.endswith('/') else f"'{s} '" for s in strings]
    print('\t'.join(strings))

if __name__ == '__main__':
    sys.exit(main())
