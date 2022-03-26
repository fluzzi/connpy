# Conn

Conn is a ssh and telnet connection manager and automation module

## Installation

pip install conn
## Automation module usage
### Standalone module
```
import conn
router = conn.node("unique name","ip/hostname", user="username", password="password")
router.run("show run")
print(router.output)
```

### Using manager configuration
```
import conn
conf = conn.configfile()
device = conf.getitem("router@office")
router = conn.node("unique name", **device, config=conf)
result = router.run("show ip int brief")
print(result)
router.interact()
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
