# Conn
[![](https://img.shields.io/pypi/v/connpy.svg?style=flat-square)](https://pypi.org/pypi/connpy/)
[![](https://img.shields.io/pypi/pyversions/connpy.svg?style=flat-square)](https://pypi.org/pypi/connpy/)
[![](https://img.shields.io/pypi/l/connpy.svg?style=flat-square)](https://github.com/fluzzi/connpy/blob/main/LICENSE)
[![](https://img.shields.io/pypi/dm/connpy.svg?style=flat-square)](https://pypi.org/pypi/connpy/)

Connpy is a ssh and telnet connection manager and automation module

## Installation

pip install connpy

## Automation module usage
### Standalone module
```
import connpy
router = connpy.node("uniqueName","ip/host", user="username", password="password")
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
nodes["router1"] = {"host": "1.1.1.1", "user": "user", "password": "password1"}
nodes["router2"] = {"host": "1.1.1.2", "user": "user", "password": "password2"}
nodes["router3"] = {"host": "1.1.1.2", "user": "user", "password": "password3"}
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
### Using variables
```
import connpy
config = connpy.configfile()
nodes = config.getitem("@office", ["router1", "router2", "router3"])
commands = []
commands.append("config t")
commands.append("interface lo {id}")
commands.append("ip add {ip} {mask}")
commands.append("end")
variables = {}
variables["router1@office"] = {"ip": "10.57.57.1"}
variables["router2@office"] = {"ip": "10.57.57.2"}
variables["router3@office"] = {"ip": "10.57.57.3"}
variables["__global__"] = {"id": "57"}
variables["__global__"]["mask"] =  "255.255.255.255"
expected = "!"
routers = connpy.nodes(nodes, config = config)
routers.run(commands, variables)
routers.test("ping {ip}", expected, variables)
for key in routers.result:
    print(key, ' ---> ', ("pass" if routers.result[key] else "fail"))
```
### Using AI
```
import connpy
conf = connpy.configfile()
organization = 'openai-org'
api_key = "openai-key"
myia = ai(conf, organization, api_key)
input = "go to router 1 and get me the full configuration"
result = myia.ask(input, dryrun = False)
print(result)
```
## Connection manager 
### Features
    - You can generate profiles and reference them from nodes using @profilename so you dont
      need to edit multiple nodes when changing password or other information.
    - Nodes can be stored on @folder or @subfolder@folder to organize your devices. Then can 
      be referenced using node@subfolder@folder or node@folder
    - If you have too many nodes. Get completion script using: conn config --completion.
      Or use fzf installing pyfzf and running conn config --fzf true
    - Much more!

### Usage:
```
usage: conn [-h] [--add | --del | --mod | --show | --debug] [node|folder]
       conn {profile,move,mv,copy,cp,list,ls,bulk,config} ...

positional arguments:
  node|folder    node[@subfolder][@folder]
                 Connect to specific node or show all matching nodes
                 [@subfolder][@folder]
                 Show all available connections globaly or in specified path
```

### Options:
```
  -h, --help         show this help message and exit
  -v, --version      Show version
  -a, --add          Add new node[@subfolder][@folder] or [@subfolder]@folder
  -r, --del, --rm    Delete node[@subfolder][@folder] or [@subfolder]@folder
  -e, --mod, --edit  Modify node[@subfolder][@folder]
  -s, --show         Show node[@subfolder][@folder]
  -d, --debug        Display all conections steps
```

### Commands:
```
  profile        Manage profiles
  move (mv)      Move node
  copy (cp)      Copy node
  list (ls)      List profiles, nodes or folders
  bulk           Add nodes in bulk
  run            Run scripts or commands on nodes
  config         Manage app config
  api            Start and stop connpy api
```

### Manage profiles:
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

### Examples:
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
## http API
With the Connpy API you can run commands on devices using http requests

### 1. List Nodes

**Endpoint**: `/list_nodes`

**Method**: `POST`

**Description**: This route returns a list of nodes. It can also filter the list based on a given keyword.

#### Request Body:

```json
{
  "filter": "<keyword>"
}
```

* `filter` (optional): A keyword to filter the list of nodes. It returns only the nodes that contain the keyword. If not provided, the route will return the entire list of nodes.

#### Response:

- A JSON array containing the filtered list of nodes.

---

### 2. Get Nodes

**Endpoint**: `/get_nodes`

**Method**: `POST`

**Description**: This route returns a dictionary of nodes with all their attributes. It can also filter the nodes based on a given keyword.

#### Request Body:

```json
{
  "filter": "<keyword>"
}
```

* `filter` (optional): A keyword to filter the nodes. It returns only the nodes that contain the keyword. If not provided, the route will return the entire list of nodes.

#### Response:

- A JSON array containing the filtered nodes.

---

### 3. Run Commands

**Endpoint**: `/run_commands`

**Method**: `POST`

**Description**: This route runs commands on selected nodes based on the provided action, nodes, and commands. It also supports executing tests by providing expected results.

#### Request Body:

```json
{
  "action": "<action>",
  "nodes": "<nodes>",
  "commands": "<commands>",
  "expected": "<expected>",
  "options": "<options>"
}
```

* `action` (required): The action to be performed. Possible values: `run` or `test`.
* `nodes` (required): A list of nodes or a single node on which the commands will be executed. The nodes can be specified as individual node names or a node group with the `@` prefix. Node groups can also be specified as arrays with a list of nodes inside the group.
* `commands` (required): A list of commands to be executed on the specified nodes.
* `expected` (optional, only used when the action is `test`): A single expected result for the test.
* `options` (optional): Array to pass options to the run command, options are: `prompt`, `parallel`, `timeout`  

#### Response:

- A JSON object with the results of the executed commands on the nodes.

---

### 4. Ask AI

**Endpoint**: `/ask_ai`

**Method**: `POST`

**Description**: This route sends to chatgpt IA a request that will parse it into an understandable output for the application and then run the request.

#### Request Body:

```json
{
  "input": "<user input request>",
  "dryrun": true or false
}
```

* `input` (required): The user input requesting the AI to perform an action on some devices or get the devices list.
* `dryrun` (optional): If set to true, it will return the parameters to run the request but it won't run it. default is false.

#### Response:

- A JSON array containing the action to run and the parameters and the result of the action.


