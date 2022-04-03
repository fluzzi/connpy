#!/usr/bin/env python3
'''
## Connection manager
```
usage: conn [-h] [--add | --del | --mod | --show | --debug] [node|folder]
       conn {profile,move,mv,copy,cp,list,ls,bulk,config} ...

positional arguments:
  node|folder    node[@subfolder][@folder]
                 Connect to specific node or show all matching nodes
                 [@subfolder][@folder]
                 Show all available connections globaly or in specified path
Options:
  -h, --help     show this help message and exit
  --add          Add new node[@subfolder][@folder] or [@subfolder]@folder
  --del, --rm    Delete node[@subfolder][@folder] or [@subfolder]@folder
  --mod, --edit  Modify node[@subfolder][@folder]
  --show         Show node[@subfolder][@folder]
  --debug, -d    Display all conections steps

Commands:
  profile        Manage profiles
  move (mv)      Move node
  copy (cp)      Copy node
  list (ls)      List profiles, nodes or folders
  bulk           Add nodes in bulk
  config         Manage app config
```

####   Manage profiles
```
usage: conn profile [-h] (--add | --del | --mod | --show) profile

positional arguments:
  profile        Name of profile to manage

options:
  -h, --help     show this help message and exit
  --add          Add new profile
  --del, --rm    Delete profile
  --mod, --edit  Modify profile
  --show         Show profile
```

####   Examples
```
   conn profile --add office-user
   conn --add @office
   conn --add @datacenter@office
   conn --add server@datacenter@office
   conn --add pc@office
   conn --show server@datacenter@office
   conn pc@office
   conn server
``` 
'''
from .core import node,nodes
from .configfile import configfile
from pkg_resources import get_distribution

__all__ = ["node", "nodes", "configfile"]
__version__ = "2.0.10"
__author__ = "Federico Luzzi"
__pdoc__ = {
    'core': False,
    'connapp': False,
}
