#!/usr/bin/env python3
'''
## Connection manager

Connpy is a SSH, SFTP, Telnet, kubectl, Docker pod, and AWS SSM connection manager and automation module for Linux, Mac, and Docker.

### Features
    - Manage connections using SSH, SFTP, Telnet, kubectl, Docker exec, and AWS SSM.
    - Set contexts to manage specific nodes from specific contexts (work/home/clients/etc).
    - You can generate profiles and reference them from nodes using @profilename so you don't
      need to edit multiple nodes when changing passwords or other information.
    - Nodes can be stored on @folder or @subfolder@folder to organize your devices. They can
      be referenced using node@subfolder@folder or node@folder.
    - If you have too many nodes, get a completion script using: conn config --completion.
      Or use fzf by installing pyfzf and running conn config --fzf true.
    - Create in bulk, copy, move, export, and import nodes for easy management.
    - Run automation scripts on network devices.
    - Use AI with a multi-agent system (Engineer/Architect) to help you manage your devices.
      Supports any LLM provider via litellm (OpenAI, Anthropic, Google, etc.).
    - Add plugins with your own scripts, and execute them remotely.
    - Fully decoupled gRPC Client/Server architecture.
    - Unified UI with syntax highlighting and theming.
    - Much more!

### Usage
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
  --service-mode     Set the backend service mode (local or remote)
  --remote           Connect to a remote connpy service via gRPC
  --theme            UI Output theme (dark, light, or path)

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

### Remote Plugin Execution
When Connpy operates in remote mode, plugins are executed **transparently on the server**:
- The client automatically downloads the plugin source code (`Parser` class context) to generate the local `argparse` structure and provide autocompletion.
- The execution phase (`Entrypoint` class) is redirected via gRPC streams to execute in the server's memory, ensuring the plugin runs securely against the server's inventory without passing sensitive data to the client.
- You can manage remote plugins using the `--remote` flag (e.g. `connpy plugin --add myplugin script.py --remote`).

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
       - The `__init__` method must initialize at least one attribute:
         - `self.parser`: An instance of `argparse.ArgumentParser`.
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

### Command Completion Support

Plugins can provide intelligent **tab completion** by defining autocompletion logic. There are two supported methods, with the tree-based approach being the most modern and recommended.

#### 1. Tree-based Completion (Recommended)

Define a function called `_connpy_tree` that returns a declarative navigation tree. This method is highly efficient, supports complex state loops, and is very simple to implement for most use cases.

```python
def _connpy_tree(info=None):
    nodes = info.get("nodes", [])
    return {
        "__exclude_used__": True,  # Filter out words already typed
        "__extra__": nodes,        # Suggest nodes at this level
        "--format": ["json", "yaml", "table"], # Fixed suggestions
        "*": {                     # Wildcard matches any positional word
            "interface1": None,
            "interface2": None,
            "--verbose": None
        }
    }
```

- **Keys**: Literal completions (exact matches).
- **`*` Key**: A wildcard that matches any positional word typed by the user.
- **`__extra__`**: A list or a callable `(words) -> list` that adds dynamic suggestions.
- **`__exclude_used__`**: (Boolean) If True, automatically filters out words already present in the command line.

#### 2. Legacy Function-based Completion

For backward compatibility or highly custom logic, you can define `_connpy_completion`.

```python
def _connpy_completion(wordsnumber, words, info=None):
    if wordsnumber == 3:
        return ["--help", "--verbose", "start", "stop"]

    elif wordsnumber == 4 and words[2] == "start":
        return info["nodes"]  # Suggest node names

    return []
```

| Parameter      | Description |
|----------------|-------------|
| `wordsnumber`  | Integer indicating the total number of words on the command line. For plugins, this typically starts at 3. |
| `words`        | A list of tokens (words) already typed. `words[0]` is always the name of the plugin. |
| `info`         | A dictionary of structured context data (`nodes`, `folders`, `profiles`, `config`). |

> In this example, if the user types `connpy myplugin start ` and presses Tab, it will suggest node names.

### Handling Unknown Arguments

Plugins can choose to accept and process unknown arguments that are **not explicitly defined** in the parser. To enable this behavior, the plugin must define the following hidden argument in its `Parser` class:

```
self.parser.add_argument(
    "--unknown-args",
    action="store_true",
    default=True,
    help=argparse.SUPPRESS
)
```

#### Behavior:

- When this argument is present, Connpy will parse the known arguments and capture any extra (unknown) ones.
- These unknown arguments will be passed to the plugin as `args.unknown_args` inside the `Entrypoint`.
- If the user does not pass any unknown arguments, `args.unknown_args` will contain the default value (`True`, unless overridden).

#### Example:

If a plugin accepts unknown tcpdump flags like this:

```
connpy myplugin -nn -s0
```

And defines the hidden `--unknown-args` flag as shown above, then:

- `args.unknown_args` inside `Entrypoint.__init__()` will be: `['-nn', '-s0']`

> This allows the plugin to receive and process arguments intended for external tools (e.g., `tcpdump`) without argparse raising an error.

#### Note:

If a plugin does **not** define `--unknown-args`, any extra arguments passed will cause argparse to fail with an unrecognized arguments error.

### Script Verification
- The `verify_script` method in `plugins.py` is used to check the plugin script's compliance with these standards.
- Non-compliant scripts will be rejected to ensure consistency and proper functionality within the plugin system.
- 
### Example Script

For a practical example of how to write a compatible plugin script, please refer to the following example:

[Example Plugin Script](https://github.com/fluzzi/awspy)

This script demonstrates the required structure and implementation details according to the plugin system's standards.

## gRPC Service Architecture
Connpy features a completely decoupled gRPC Client/Server architecture. You can run Connpy as a standalone background service and connect to it remotely via the CLI or other clients.

### 1. Start the Server
Start the gRPC service by running:
```bash
connpy api -s 50051
```
The server will handle all configurations, connections, AI sessions, and plugin execution locally on the machine it runs on.

### 2. Connect the Client
Configure your local CLI client to connect to the remote server:
```bash
connpy config --service-mode remote
connpy config --remote-host localhost:50051
```
Once configured, all commands (`connpy node`, `connpy list`, `connpy ai`, etc.) will execute transparently on the remote server via thin-client proxies. You can revert back to standalone execution at any time by running `connpy config --service-mode local`.

### Programmatic Access (gRPC & SOA)
Developers can build their own applications using the Connpy backend by utilizing the `ServiceProvider`:

```python
from connpy.services.provider import ServiceProvider
services = ServiceProvider(config, mode="remote", remote_host="localhost:50051")
nodes = services.nodes.list_nodes()
```


## Automation module
The automation module
### Standalone module
```
import connpy
router = connpy.node("uniqueName","ip/host", user="user", password="pass")
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
nodes["router1"] = {"host": "1.1.1.1", "user": "user", "password": "pass1"}
nodes["router2"] = {"host": "1.1.1.2", "user": "user", "password": "pass2"}
nodes["router3"] = {"host": "1.1.1.2", "user": "user", "password": "pass3"}
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
# Uses models and API keys from config, or override them:
myai = connpy.ai(conf, engineer_model="gemini/gemini-2.5-flash", engineer_api_key="your-key")
result = myai.ask("go to router1 and show me the running configuration")
print(result["response"])
# Streaming is enabled by default for CLI, disable for programmatic use:
result = myai.ask("show interfaces on all routers", stream=False)
print(result["response"])
```

#### AI Plugin Tool Registration
Plugins can register custom tools with the AI system using `register_ai_tool()` in their `Preload` class:
```
def _register_my_tools(ai_instance):
    tool_def = {
        "type": "function",
        "function": {
            "name": "my_custom_tool",
            "description": "Does something useful.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }
        }
    }
    ai_instance.register_ai_tool(
        tool_definition=tool_def,
        handler=my_handler_function,
        target="engineer",  # or "architect" or "both"
        engineer_prompt="- My tool: does X.",
        architect_prompt="  * My tool (my_custom_tool)."
    )

class Preload:
    def __init__(self, connapp):
        connapp.ai.modify(_register_my_tools)
```

## Developer Notes (SOA Architecture)
As of version 2.0, Connpy has migrated to a **Service-Oriented Architecture (SOA)**:
- **`connpy/cli/`**: Contains all CLI handlers. These are responsible for argument parsing, user interaction (via `inquirer`), and visual output (via `printer`).
- **`connpy/services/`**: Contains pure logic services (Node, Profile, Execution, etc.).
- **Zero-Print Policy**: Services must never use `print()`. All output must be returned as data structures or generators to the caller (CLI handlers).
- **ServiceProvider**: Access services via `connapp.services`. This allows transparent switching between local and remote (gRPC) backends without modifying CLI logic.
'''
from .core import node,nodes
from .configfile import configfile
from .connapp import connapp
from .api import *
from .ai import ai
from .plugins import Plugins
from ._version import __version__
from . import printer

__all__ = ["node", "nodes", "configfile", "connapp", "ai", "Plugins", "printer"]
__author__ = "Federico Luzzi"
__pdoc__ = {
    'core': False,
    'completion': False,
    'api': False,
    'plugins': False,
    'core_plugins': False,
    'hooks': False,
    'connapp.start': False,
    'ai.deferred_class_hooks': False,
    'configfile.deferred_class_hooks': False,
    'node.deferred_class_hooks': False,
    'nodes.deferred_class_hooks': False,
    'connapp': False,
    'connapp.encrypt': True,
    'printer': False
}
