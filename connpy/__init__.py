#!/usr/bin/env python3
'''
## Connection manager

Connpy is a connection manager that allows you to store nodes to connect them fast and password free.

### Features
    - You can generate profiles and reference them from nodes using @profilename 
      so you dont need to edit multiple nodes when changing password or other 
      information.
    - Nodes can be stored on @folder or @subfolder@folder to organize your 
      devices. Then can be referenced using node@subfolder@folder or node@folder
    - Much more!

### Usage
```
usage: conn [-h] [--add | --del | --mod | --show | --debug] [node|folder]
       conn {profile,move,mv,copy,cp,list,ls,bulk,config} ...

positional arguments:
  node|folder    node[@subfolder][@folder]
                 Connect to specific node or show all matching nodes
                 [@subfolder][@folder]
                 Show all available connections globaly or in specified path
Options:
  -h, --help         show this help message and exit
  -a, --add          Add new node[@subfolder][@folder] or [@subfolder]@folder
  -r, --del, --rm    Delete node[@subfolder][@folder] or [@subfolder]@folder
  -e, --mod, --edit  Modify node[@subfolder][@folder]
  -s, --show         Show node[@subfolder][@folder]
  -d, --debug        Display all conections steps

Commands:
  profile        Manage profiles
  move (mv)      Move node
  copy (cp)      Copy node
  list (ls)      List profiles, nodes or folders
  bulk           Add nodes in bulk
  config         Manage app config
```

###   Manage profiles
```
usage: conn profile [-h] (--add | --del | --mod | --show) profile

positional arguments:
  profile        Name of profile to manage

options:
  -h, --help         show this help message and exit
  -a, --add          Add new profile
  -r, --del, --rm    Delete profile
  -e, --mod, --edit  Modify profile
  -s, --show         Show profile

```

###   Examples
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

## Automation module
the automation module

### Standalone module
```
import connpy
router = connpy.node("unique name","ip/hostname", user="user", password="pass")
router.run(["term len 0","show run"])
print(router.output)
hasip = router.test("show ip int brief","1.1.1.1")
if hasip:
    print("Router has ip 1.1.1.1")
else:
    print("router does not have ip 1.1.1.1")
```

### Using manager configuration
```
import connpy
conf = connpy.configfile()
device = conf.getitem("server@office")
server = connpy.node("unique name", **device, config=conf)
result = server.run(["cd /", "ls -la"])
print(result)
```
### Running parallel tasks 
```
import connpy
conf = connpy.configfile()
#You can get the nodes from the config from a folder and fitlering in it
nodes = conf.getitem("@office", ["router1", "router2", "router3"])
#You can also get each node individually:
nodes = {}
nodes["router1"] = conf.getitem("router1@office")
nodes["router2"] = conf.getitem("router2@office")
nodes["router10"] = conf.getitem("router10@datacenter")
#Also, you can create the nodes manually:
nodes = {}
nodes["router1"] = {"host": "1.1.1.1", "user": "username", "password": "pass1"}
nodes["router2"] = {"host": "1.1.1.2", "user": "username", "password": "pass2"}
nodes["router3"] = {"host": "1.1.1.2", "user": "username", "password": "pass3"}
#Finally you run some tasks on the nodes
mynodes = connpy.nodes(nodes, config = conf)
result = mynodes.test(["show ip int br"], "1.1.1.2")
for i in result:
    print("---" + i + "---")
    print(result[i])
    print()
# Or for one specific node
mynodes.router1.run(["term len 0". "show run"], folder = "/home/user/logs")
```

'''
from .core import node,nodes
from .configfile import configfile
from .connapp import connapp
from pkg_resources import get_distribution

__all__ = ["node", "nodes", "configfile", "connapp"]
__version__ = "2.0.3"
__author__ = "Federico Luzzi"
__pdoc__ = {
    'core': False,
}
