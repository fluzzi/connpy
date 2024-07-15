<p align="center">
  <img src="https://nginx.gederico.dynu.net/images/CONNPY-resized.png" alt="App Logo">
</p>


# Connpy
[![](https://img.shields.io/pypi/v/connpy.svg?style=flat-square)](https://pypi.org/pypi/connpy/)
[![](https://img.shields.io/pypi/pyversions/connpy.svg?style=flat-square)](https://pypi.org/pypi/connpy/)
[![](https://img.shields.io/pypi/l/connpy.svg?style=flat-square)](https://github.com/fluzzi/connpy/blob/main/LICENSE)
[![](https://img.shields.io/pypi/dm/connpy.svg?style=flat-square)](https://pypi.org/pypi/connpy/)

Connpy is a SSH, SFTP, Telnet, kubectl, and Docker pod connection manager and automation module for Linux, Mac, and Docker.


## Installation

pip install connpy

### Run it in Windows using docker
```
git clone https://github.com/fluzzi/connpy
docker compose -f path/to/folder/docker-compose.yml build
docker compose -f path/to/folder/docker-compose.yml run -it connpy-app
```

## Connection manager 
### Privacy Policy

Connpy is committed to protecting your privacy. Our privacy policy explains how we handle user data:

- **Data Access**: Connpy accesses data necessary for managing remote host connections, including server addresses, usernames, and passwords. This data is stored locally on your machine and is not transmitted or shared with any third parties.
- **Data Usage**: User data is used solely for the purpose of managing and automating SSH and Telnet connections.
- **Data Storage**: All connection details are stored locally and securely on your device. We do not store or process this data on our servers.
- **Data Sharing**: We do not share any user data with third parties.

### Google Integration

Connpy integrates with Google services for backup purposes:

- **Configuration Backup**: The app allows users to store their device information in the app configuration. This configuration can be synced with Google services to create backups.
- **Data Access**: Connpy only accesses its own files and does not access any other files on your Google account.
- **Data Usage**: The data is used solely for backup and restore purposes, ensuring that your device information and configurations are safe and recoverable.
- **Data Sharing**: Connpy does not share any user data with third parties, including Google. The backup data is only accessible by the user.

For more detailed information, please read our [Privacy Policy](https://connpy.gederico.dynu.net/fluzzi32/connpy/src/branch/main/PRIVATE_POLICY.md).


### Features
    - Manage connections using SSH, SFTP, Telnet, kubectl, and Docker exec.
    - Set contexts to manage specific nodes from specific contexts (work/home/clients/etc).
    - You can generate profiles and reference them from nodes using @profilename so you don't
      need to edit multiple nodes when changing passwords or other information.
    - Nodes can be stored on @folder or @subfolder@folder to organize your devices. They can
      be referenced using node@subfolder@folder or node@folder.
    - If you have too many nodes, get a completion script using: conn config --completion.
      Or use fzf by installing pyfzf and running conn config --fzf true.
    - Create in bulk, copy, move, export, and import nodes for easy management.
    - Run automation scripts on network devices.
    - Use GPT AI to help you manage your devices.
    - Add plugins with your own scripts.
    - Much more!

### Usage:
```
usage: conn [-h] [--add | --del | --mod | --show | --debug] [node|folder] [--sftp]
       conn {profile,move,mv,copy,cp,list,ls,bulk,export,import,ai,run,api,plugin,config,sync,context} ...

positional arguments:
  node|folder        node[@subfolder][@folder]
                     Connect to specific node or show all matching nodes
                     [@subfolder][@folder]
                     Show all available connections globally or in specified path

options:
  -h, --help         show this help message and exit
  -v, --version      Show version
  -a, --add          Add new node[@subfolder][@folder] or [@subfolder]@folder
  -r, --del, --rm    Delete node[@subfolder][@folder] or [@subfolder]@folder
  -e, --mod, --edit  Modify node[@subfolder][@folder]
  -s, --show         Show node[@subfolder][@folder]
  -d, --debug        Display all conections steps
  -t, --sftp         Connects using sftp instead of ssh

Commands:
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
  context         Manage contexts with regex matching
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
   #Add new profile
   conn profile --add office-user
   #Add new folder
   conn --add @office
   #Add new subfolder
   conn --add @datacenter@office
   #Add node to subfolder
   conn --add server@datacenter@office
   #Add node to folder
   conn --add pc@office
   #Show node information
   conn --show server@datacenter@office
   #Connect to nodes
   conn pc@office
   conn server
   #Create and set new context
   conn context -a office .*@office
   conn context --set office
   #Run a command in a node
   conn run server ls -la
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
- The plugin script must define specific classes with particular attributes and methods. Each class serves a distinct role within the plugin's architecture:
  1. **Class `Parser`**:
     - **Purpose**: Handles parsing of command-line arguments.
     - **Requirements**:
       - Must contain only one method: `__init__`.
       - The `__init__` method must initialize at least two attributes:
         - `self.parser`: An instance of `argparse.ArgumentParser`.
         - `self.description`: A string containing the description of the parser.
  2. **Class `Entrypoint`**:
     - **Purpose**: Acts as the entry point for plugin execution, utilizing parsed arguments and integrating with the main application.
     - **Requirements**:
       - Must have an `__init__` method that accepts exactly three parameters besides `self`:
         - `args`: Arguments passed to the plugin.
         - The parser instance (typically `self.parser` from the `Parser` class).
         - The Connapp instance to interact with the Connpy app.
  3. **Class `Preload`**:
     - **Purpose**: Performs any necessary preliminary setup or configuration independent of the main parsing and entry logic.
   - **Requirements**:
     - Contains at least an `__init__` method that accepts parameter connapp besides `self`.

### Class Dependencies and Combinations
- **Dependencies**:
  - `Parser` and `Entrypoint` are interdependent and must both be present if one is included.
  - `Preload` is independent and may exist alone or alongside the other classes.
- **Valid Combinations**:
  - `Parser` and `Entrypoint` together.
  - `Preload` alone.
  - All three classes (`Parser`, `Entrypoint`, `Preload`).

### Preload Modifications and Hooks

In the `Preload` class of the plugin system, you have the ability to customize the behavior of existing classes and methods within the application through a robust hooking system. This documentation explains how to use the `modify`, `register_pre_hook`, and `register_post_hook` methods to tailor plugin functionality to your needs.

#### Modifying Classes with `modify`
The `modify` method allows you to alter instances of a class at the time they are created or after their creation. This is particularly useful for setting or modifying configuration settings, altering default behaviors, or adding new functionalities to existing classes without changing the original class definitions.

- **Usage**: Modify a class to include additional configurations or changes
- **Modify Method Signature**:
  - `modify(modification_method)`: A function that is invoked with an instance of the class as its argument. This function should perform any modifications directly on this instance.
- **Modification Method Signature**:
  - **Arguments**:
    - `cls`:  This function accepts a single argument, the class instance, which it then modifies.
  - **Modifiable Classes**:
    - `connapp.config`
    - `connapp.node`
    - `connapp.nodes`
    - `connapp.ai`
  - ```python
    def modify_config(cls):
        # Example modification: adding a new attribute or modifying an existing one
        cls.new_attribute = 'New Value'

    class Preload:
        def __init__(self, connapp):
            # Applying modification to the config class instance
            connapp.config.modify(modify_config)
    ```

#### Implementing Method Hooks
There are 2 methods that allows you to define custom logic to be executed before (`register_pre_hook`) or after (`register_post_hook`) the main logic of a method. This is particularly useful for logging, auditing, preprocessing inputs, postprocessing outputs or adding functionalities.

  - **Usage**: Register hooks to methods to execute additional logic before or after the main method execution.
- **Registration Methods Signature**:
  - `register_pre_hook(pre_hook_method)`: A function that is invoked before the main method is executed. This function should do preprocessing of the arguments.
  - `register_post_hook(post_hook_method)`: A function that is invoked after the main method is executed. This function should do postprocessing of the outputs.
- **Method Signatures for Pre-Hooks**
  - `pre_hook_method(*args, **kwargs)`
  - **Arguments**:
    - `*args`, `**kwargs`: The arguments and keyword arguments that will be passed to the method being hooked. The pre-hook function has the opportunity to inspect and modify these arguments before they are passed to the main method.
  - **Return**:
    - Must return a tuple `(args, kwargs)`, which will be used as the new arguments for the main method. If the original arguments are not modified, the function should return them as received.
- **Method Signatures for Post-Hooks**:
  - `post_hook_method(*args, **kwargs)`
  - **Arguments**:
    - `*args`, `**kwargs`: The arguments and keyword arguments that were passed to the main method.
      - `kwargs["result"]`: The value returned by the main method. This allows the post-hook to inspect and even alter the result before it is returned to the original caller.
  - **Return**:
    - Can return a modified result, which will replace the original result of the main method, or simply return `kwargs["result"]` to return the original method result.    
  - ```python
    def pre_processing_hook(*args, **kwargs):
        print("Pre-processing logic here")
        # Modify arguments or perform any checks
        return args, kwargs  # Return modified or unmodified args and kwargs

    def post_processing_hook(*args, **kwargs):
        print("Post-processing logic here")
        # Modify the result or perform any final logging or cleanup
        return kwargs["result"]  # Return the modified or unmodified result

    class Preload:
        def __init__(self, connapp):
            # Registering a pre-hook
            connapp.ai.some_method.register_pre_hook(pre_processing_hook)

            # Registering a post-hook
            connapp.node.another_method.register_post_hook(post_processing_hook)
    ```
  

### Executable Block
- The plugin script can include an executable block:
  - `if __name__ == "__main__":`
  - This block allows the plugin to be run as a standalone script for testing or independent use.

### Script Verification
- The `verify_script` method in `plugins.py` is used to check the plugin script's compliance with these standards.
- Non-compliant scripts will be rejected to ensure consistency and proper functionality within the plugin system.
 
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
myia = connpy.ai(conf, organization, api_key)
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


