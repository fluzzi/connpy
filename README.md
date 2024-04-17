# Conn
[![](https://img.shields.io/pypi/v/connpy.svg?style=flat-square)](https://pypi.org/pypi/connpy/)
[![](https://img.shields.io/pypi/pyversions/connpy.svg?style=flat-square)](https://pypi.org/pypi/connpy/)
[![](https://img.shields.io/pypi/l/connpy.svg?style=flat-square)](https://github.com/fluzzi/connpy/blob/main/LICENSE)
[![](https://img.shields.io/pypi/dm/connpy.svg?style=flat-square)](https://pypi.org/pypi/connpy/)

Connpy is a ssh and telnet connection manager and automation module for Linux, Mac and Docker

## Installation

pip install connpy

### Run it in Windows using docker
```
git clone https://github.com/fluzzi/connpy
docker compose -f path/to/folder/docker-compose.yml build
docker compose -f path/to/folder/docker-compose.yml run -it connpy-app
```

## Connection manager 
### Features
    - You can generate profiles and reference them from nodes using @profilename so you dont
      need to edit multiple nodes when changing password or other information.
    - Nodes can be stored on @folder or @subfolder@folder to organize your devices. Then can 
      be referenced using node@subfolder@folder or node@folder
    - If you have too many nodes. Get completion script using: conn config --completion.
      Or use fzf installing pyfzf and running conn config --fzf true
    - Create in bulk, copy, move, export and import nodes for easy management.
    - Run automation scripts in network devices.
    - use GPT AI to help you manage your devices.
    - Add plugins with your own scripts.
    - Much more!

### Usage:
```
usage: conn [-h] [--add | --del | --mod | --show | --debug] [node|folder] [--sftp]
       conn {profile,move,mv,copy,cp,list,ls,bulk,export,import,ai,run,api,plugin,config} ...

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
  -t, --sftp         Connects using sftp instead of ssh
```

### Commands:
```
  profile         Manage profiles
  move(mv)        Move node
  copy(cp)        Copy node
  list(ls)        List profiles, nodes or folders
  bulk            Add nodes in bulk
  export          Export connection folder to Yaml file
  import          Import connection folder to config from Yaml file
  ai              Make request to an AI
  run             Run scripts or commands on nodes
  api             Start and stop connpy api
  plugin          Manage plugins
  config          Manage app config
  sync            Sync config with Google
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
## Plugin Requirements for Connpy

### General Structure
- The plugin script must be a Python file.
- Only the following top-level elements are allowed in the plugin script:
  - Class definitions
  - Function definitions
  - Import statements
  - The `if __name__ == "__main__":` block for standalone execution
  - Pass statements

### Specific Class Requirements
- The plugin script must define at least two specific classes:
  1. **Class `Parser`**:
     - Must contain only one method: `__init__`.
     - The `__init__` method must initialize at least two attributes:
       - `self.parser`: An instance of `argparse.ArgumentParser`.
       - `self.description`: A string containing the description of the parser.
  2. **Class `Entrypoint`**:
     - Must have an `__init__` method that accepts exactly three parameters besides `self`:
       - `args`: Arguments passed to the plugin.
       - The parser instance (typically `self.parser` from the `Parser` class).
       - The Connapp instance to interact with the Connpy app.

### Executable Block
- The plugin script can include an executable block:
  - `if __name__ == "__main__":`
  - This block allows the plugin to be run as a standalone script for testing or independent use.

### Script Verification
- The `verify_script` method in `plugins.py` is used to check the plugin script's compliance with these standards.
- Non-compliant scripts will be rejected to ensure consistency and proper functionality within the plugin system.
- 
### Example Script

For a practical example of how to write a compatible plugin script, please refer to the following example:

[Example Plugin Script](https://github.com/fluzzi/awspy)

This script demonstrates the required structure and implementation details according to the plugin system's standards.

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


