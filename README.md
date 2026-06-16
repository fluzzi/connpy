<p align="center">
  <img src="https://nginx.gederico.dynu.net/images/CONNPY-resized.png" alt="App Logo">
</p>


# Connpy (v6.0.3)
[![](https://img.shields.io/pypi/v/connpy.svg?style=flat-square)](https://pypi.org/pypi/connpy/)
[![](https://img.shields.io/pypi/pyversions/connpy.svg?style=flat-square)](https://pypi.org/pypi/connpy/)
[![](https://img.shields.io/pypi/dm/connpy.svg?style=flat-square&cacheSeconds=86400)](https://pypi.org/pypi/connpy/)
[![](https://img.shields.io/badge/platform-linux%20%7C%20macos%20%7C%20docker-blue?style=flat-square)](https://github.com/fluzzi/connpy)
[![](https://img.shields.io/badge/backend-gRPC-blue?style=flat-square)](https://github.com/fluzzi/connpy)
[![](https://img.shields.io/badge/AI%20Core-LiteLLM-green?style=flat-square)](https://github.com/fluzzi/connpy)
[![](https://img.shields.io/badge/MCP-compatible-orange?style=flat-square)](https://modelcontextprotocol.io)
[![](https://img.shields.io/pypi/l/connpy.svg?style=flat-square)](https://github.com/fluzzi/connpy/blob/main/LICENSE)

**Connpy** is a powerful Connection Manager and Network Automation Platform for Linux, Mac, and Docker. It provides a unified interface for **SSH, SFTP, Telnet, kubectl, Docker pods, and AWS SSM**.

The v6 release introduces a comprehensive **AI Copilot** and **AI Playbook Engine**, transforming your terminal into an interactive network assistant that understands your device outputs, configures parameters safely, and runs simulations.


---

## 1. 🤖 AI System

### 1a. Terminal Copilot (Ctrl+Space)
Invoke the context-aware AI Copilot directly inside any active terminal session by pressing **`Ctrl + Space`**. 
* **Context Modes**: Cycles through `LINES` (sends raw scroll buffer), `SINGLE` (captures exactly one command + output block), and `RANGE` (logical group of recent commands) using **`Ctrl+Up/Down`**.
* **Slash Commands (`/`)**: Control the AI persona and safety settings:
  * `/architect` / `/engineer`: Swaps the agent between high-level strategist and technical executor.
  * `/trust` / `/untrust`: Configures auto-run behavior for suggested non-destructive commands.
  * `/os [system]`: Manually overrides target OS parsing rules (e.g. `/os cisco_ios`).
  * `/prompt [regex]`: Overrides command prompt detection bounds.
  * `/clear`: Clear context history.

### 1b. AI Chat (conn ai)
Start a standalone persistent session with the AI Copilot. Manage sessions using `--list`, `--resume`, `--session <id>` (to restore a specific history), `--delete <id>`, or send a quick single-shot question directly from the terminal prompt:
```bash
conn ai "how do i check bgp summary on cisco?"
```

### 1c. MCP Integration
Connect to external data sources and tools dynamically via the Model Context Protocol (MCP). Use the interactive wizard or command actions to configure MCP servers:
```bash
conn ai --mcp
```


---

## 2. ⚙️ Automation & Playbooks

### 2a. Quick Run (conn run)
Run commands in parallel directly on target nodes or folder structures:
```bash
conn run router1 "show interface"
```

### 2b. YAML Playbook Engine
Execute complex structured automation playbooks defined in YAML configuration files. Supports multi-task execution, variables (using global, per-node, or regex matching definitions), timeouts, and variable parallel execution bounds.

```yaml
# example_playbook.yaml
- name: Verify Network Operations
  hosts: "@office"
  parallel: true
  tasks:
    - name: Get interface brief
      run: "show ip interface brief"
    - name: Check OSPF state
      run: "show ip ospf neighbor"
      test: "FULL"
```
Execute using the playbooks runner:
```bash
conn run example_playbook.yaml
```

### 2c. AI-Assisted Automation
Leverage AI to generate playbook templates (`--generate-ai`), simulate command changes before execution (`--preflight-ai`), or analyze consolidated execution logs post-run (`--analyze`). Use `--test "expected text1" "expected text2"` to specify assert-style output validations.
* *To generate an empty template:* `conn run --generate`


---

## 3. 📂 Inventory Management

### 3a. Nodes
Manage connections using standard commands: add (`conn --add node1`), edit (`conn --mod node1`), delete (`conn --del node1`), show configuration (`conn --show node1`), or connect (`conn node1`).

### 3b. Profiles
Define credentials and templates globally and reference them inside node fields using the `@profile_name` placeholder. Manage profiles interactively or via commands:
```bash
conn profile -a profile_name
# Or equivalently:
conn -a profile profile_name
```
During the interactive `conn --add` prompt, you can input `@profile_name` in the **username** or **password** fields to reference it.

### 3c. Folders, Move, Copy, List
Organize nodes into logical folder hierarchies (`@office`, `@datacenter@office`). Move items (`conn move [src] [dst]`), copy (`conn copy [src] [dst]`), or list items with custom filters and formatting:
```bash
conn list nodes --filter ".*-prod" --format "{name} ({host}) runs {protocol}"
```

### 3d. Bulk, Export, Import
Bulk import connections from formatted text files (`conn bulk -f nodes.txt`), or export/import connection folders using YAML configurations (`conn export @folder > backup.yaml` / `conn import backup.yaml`).

### 3e. Tags System
Customize connection settings dynamically using tags. Configure per-node settings like custom OS types (`os`), prompt regex rules (`prompt`), and page length triggers (`screen_length_command`).
```yaml
# Custom tags dictionary (YANG / VSR context)
tags: { "os": "cisco_ios", "prompt": ".*#", "screen_length_command": "terminal length 0" }
```


---

## 4. 🔌 Protocols & Connection Features

### 4a. SSH / SFTP / Telnet / kubectl / Docker / AWS SSM
Connect to various architectures using native protocols:
* **SSH / Telnet**: Standard CLI protocols.
* **SFTP**: Transfer files securely (`conn --sftp node`).
* **Docker**: Connect directly to local container names (host set to container name/ID).
* **Kubernetes (kubectl)**: Connect to pods (namespace customizable via options).
* **AWS SSM**: Connect to EC2 instances using Instance IDs as hosts.

### 4b. Jumphosts
Support for single or chained intermediate gateway nodes (SSH, SSM, kubectl, or docker jumphosts) to tunnel traffic safely into target environments.

### 4c. Debug Mode, Keepalive, Logging
Track connection steps (`conn --debug node`), set idle keepalive intervals (`conn config --keepalive <seconds>`), or define dynamic output log files using variables like `${unique}`, `${host}`, `${port}`, `${user}`, `${protocol}`, or `${date 'format'}`.


---

## 5. 🖥️ Remote Capture (conn capture - Core Plugin)
Perform remote packet capture (`tcpdump`) on hosts over secure SSH reverse tunnels and stream packets live into your local Wireshark GUI:
```bash
conn capture router1 eth0 -w -f "port 80"
```
* **Requirements**: Local installation of Wireshark or `tshark` is required for live piping (`-w`).
* **Advanced flags**: Specify network namespaces (`--ns <name>`), custom filters (`-f <filter>`), or configure the Wireshark local path (`--set-wireshark-path`).


---

## 6. 🛡️ Context Filtering
Prevent accidental command execution in production by setting active regex contexts. This hides non-matching inventory items and restricts execution scope:
```bash
conn context production -a --regex ".*-prod"
conn context production --set
```
* **Manage Contexts**: List defined filters (`conn context --ls`), show context details (`conn context production -s`), or delete contexts (`conn context production -r`).


---

## 7. 🔌 Plugin System
Extend `connpy` features and hook into core execution events (pre/post hooks) by writing Python scripts. Add, update, delete, or list plugins locally, or execute them on remote instances:
```bash
conn plugin --add my_plugin script.py
conn plugin --update my_plugin script.py
conn plugin --remote --sync
```


---

## 8. ⚙️ gRPC Client-Server Architecture

### 8a. Server (start/stop/restart/debug)
Execute tasks on a centralized remote host. Start gRPC server (`conn api -s 50051`), stop (`conn api -x`), restart (`conn api -r`), or debug in the foreground (`conn api -d`).

### 8b. Client Config
Shift the local CLI to communicate with a remote server instance:
```bash
conn config --service-mode remote
conn config --remote localhost:50051
```

### 8c. User Management
Manage server-side user credentials for distributed setups:
```bash
conn user --add username
conn user --list
conn user --regen-password username
```
Use `--path` to specify custom configuration folders in server Mode B.

### 8d. SSO / OIDC
Configure identity providers (e.g. Authelia, Keycloak) for SSO gRPC authentication using the interactive wizard:
```bash
conn sso --add provider_name
```

### 8e. Login / Logout
Authenticate client sessions (`conn login [username]`), check connection status (`conn login --status`), or close sessions (`conn logout`).


---

## 9. ⚡ Installation & Configuration

### 9a. pip install
```bash
pip install connpy
```

### 9b. Shell Completion + FZF
Install autocompletions and fuzzy-search wrappers into your shell profile:
```bash
eval "$(conn config --completion bash)"
eval "$(conn config --fzf-wrapper bash)"
```

### 9c. conn config options
View configuration details (`conn config`) or customize variables like case sensitivity (`--allow-uppercase`), FZF list picker (`--fzf true`), configurations directory (`--configfolder`), or persistent AI API keys and models (`--engineer-model`).

### 9d. Theming
Customize CLI panel styles and colors by pointing to built-in presets or external YAML styles:
```bash
conn config --theme /path/to/theme.yaml
```


---

## 10. 🔒 Privacy, Security & Synchronization (conn sync)
Encrypts inventory and profiles locally via RSA/OAEP. Backup and sync configurations to Google Drive manually (`conn sync --once`, `--list`, `--restore`) or schedule auto-sync. Segregate restores (`--nodes` / `--config`) or sync remote nodes with `--sync-remote`.


---

## 11. 🐍 Python API
Embed connection and automation routines programmatically in Python:

```python
import connpy

# 1. Direct single node interaction
router = connpy.node("router1", "1.1.1.1", user="admin")
router.run(["show ip int brief"])
print(router.output)

# 2. Parallel nodes execution with variables
config = connpy.configfile()
nodes_info = config.getitem("@office", ["router1", "router2"])
routers = connpy.nodes(nodes_info, config=config)
variables = {
    "router1@office": {"id": "1"},
    "__global__": {"mask": "255.255.255.0"}
}
routers.run(["interface lo{id}", "ip address 10.0.0.{id} {mask}"], variables)

# 3. AI Copilot prompts
myai = connpy.ai(connpy.configfile())
response = myai.ask("Show BGP status.")
print(response)
```
*Supports additional programmatic features like `node.test()`, `node.interact()`, `configfile.encrypt()`, `connapp` embeds, and `ClassHook` / `MethodHook` plugin hooks.*


---

## 12. 🐳 Docker Deployment
Run `connpy` containerized and silent:
```bash
docker compose run --rm connpy-app [command]
```
Add `alias conn='docker compose run --rm connpy-app'` to your shell for a transparent container experience.


---

## 13. 📜 License
[PolyForm Noncommercial 1.0.0](LICENSE)
