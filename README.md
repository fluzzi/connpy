# Conn

Connpy is a ssh and telnet connection manager and automation module

## Installation

pip install connpy

## Automation module usage
### Standalone module
```
import connpy
router = connpy.node("unique name","ip/hostname", user="username", password="password")
router.run(["term len 0","show run"])
print(router.output)
hasip = router.test("show ip int brief","1.1.1.1")
if hasip:
    print("Router has ip 1.1.1.1")
else:
    print("router don't has ip 1.1.1.1")
```

### Using manager configuration
```
import connpy
conf = connpy.configfile()
device = conf.getitem("router@office")
router = connpy.node("unique name", **device, config=conf)
result = router.run("show ip int brief")
print(result)
```
### Running parallel tasks on multiple devices 
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
nodes["router1"] = {"host": "1.1.1.1", "user": "username", "password": "password1"}
nodes["router2"] = {"host": "1.1.1.2", "user": "username", "password": "password2"}
nodes["router3"] = {"host": "1.1.1.2", "user": "username", "password": "password3"}
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

## Connection manager usage
```
usage: conn [-h] [--add | --del | --mod | --show | --debug] [node|folder]
       conn {profile,move,mv,copy,cp,list,ls,bulk,config} ...

positional arguments:
  node|folder    node[@subfolder][@folder]
                 Connect to specific node or show all matching nodes
                 [@subfolder][@folder]
                 Show all available connections globaly or in specified path
```

####        Options:
```
  -h, --help     show this help message and exit
  --add          Add new node[@subfolder][@folder] or [@subfolder]@folder
  --del, --rm    Delete node[@subfolder][@folder] or [@subfolder]@folder
  --mod, --edit  Modify node[@subfolder][@folder]
  --show         Show node[@subfolder][@folder]
  --debug, -d    Display all conections steps
```

####    Commands:
```
  profile        Manage profiles
  move (mv)      Move node
  copy (cp)      Copy node
  list (ls)      List profiles, nodes or folders
  bulk           Add nodes in bulk
  config         Manage app config
```

####   Manage profiles:
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

####   Examples:
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
