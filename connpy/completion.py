import sys
import os
import json

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

def main():
    home = os.path.expanduser("~")
    defaultdir = home + '/.config/conn'
    defaultfile = defaultdir + '/config.json'
    jsonconf = open(defaultfile)
    config = json.load(jsonconf)
    nodes = _getallnodes(config)
    folders = _getallfolders(config)
    profiles = list(config["profiles"].keys())
    wordsnumber = int(sys.argv[1])
    words = sys.argv[3:]
    if wordsnumber == 2:
        strings=["--add", "--del", "--rm", "--edit", "--mod", "--show", "mv", "move", "ls", "list", "cp", "copy", "profile", "run", "bulk", "config", "api", "--help"]
        strings.extend(nodes)
        strings.extend(folders)

    elif wordsnumber == 3:
        strings=[]
        if words[0] == "profile":
            strings=["--add", "--rm", "--del", "--edit", "--mod", "--show", "--help"]
        if words[0] == "config":
            strings=["--allow-uppercase", "--keepalive", "--completion", "--fzf", "--configfolder", "--help"]
        if words[0] == "api":
            strings=["--start", "--stop", "--restart", "--debug", "--help"]
        if words[0] in ["--mod", "--edit", "-e", "--show", "-s", "--add", "-a", "--rm", "--del", "-r"]:
            strings=["profile"]
        if words[0] in ["list", "ls"]:
            strings=["profiles", "nodes", "folders"]
        if words[0] in ["bulk", "mv", "cp", "copy", "run"]:
            strings=["--help"]
        if words[0] in ["--rm", "--del", "-r"]:
            strings.extend(folders)
        if words[0] in ["--rm", "--del", "-r", "--mod", "--edit", "-e", "--show", "-s", "mv", "move", "cp", "copy", "run"]:
            strings.extend(nodes)

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

    print(*strings)

if __name__ == '__main__':
    sys.exit(main())
