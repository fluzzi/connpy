<p align="center">
  <img src="https://nginx.gederico.dynu.net/images/CONNPY-resized.png" alt="App Logo">
</p>


# Connpy
[![](https://img.shields.io/pypi/v/connpy.svg?style=flat-square)](https://pypi.org/pypi/connpy/)
[![](https://img.shields.io/pypi/pyversions/connpy.svg?style=flat-square)](https://pypi.org/pypi/connpy/)
[![](https://img.shields.io/pypi/l/connpy.svg?style=flat-square)](https://github.com/fluzzi/connpy/blob/main/LICENSE)
[![](https://img.shields.io/pypi/dm/connpy.svg?style=flat-square)](https://pypi.org/pypi/connpy/)

**Connpy** is a powerful Connection Manager and Network Automation Platform for Linux, Mac, and Docker. It provides a unified interface for **SSH, SFTP, Telnet, kubectl, Docker pods, and AWS SSM**.

The v6 release introduces the **AI Copilot**, an interactive terminal assistant that understands your network context and helps you manage your infrastructure more intelligently.


## 🤖 AI Copilot (New in v6)
The AI Copilot is deeply integrated into your terminal workflow:
- **Terminal Context Awareness**: The Copilot can "see" your screen output, helping you diagnose errors or analyze command results in real-time.
- **Dynamic Context Selection**: Flexibly select single, range, or line-based terminal blocks to feed the Copilot, filtering out interactive scrolling garbage automatically (e.g., Cisco IOS/XR scrolling, paginators).
- **Hybrid Multi-Agent System**: Automatically escalates complex tasks between the **Network Engineer** (execution) and the **Network Architect** (strategy).
- **MCP Integration**: Dynamically load tools from external providers (6WIND, AWS, etc.) via the Model Context Protocol.
- **Flexible Auth & Keyless AI**: Support for advanced LiteLLM credentials (`--engineer-auth` / `--architect-auth`) allowing keyless local models (Ollama), cloud engines (Vertex AI), or custom endpoints.
- **Enhanced Session Management**: Uniquely generated sessions, robust pagination, and interactive styling translating prompt themes directly to terminal escapes.
- **Semantic Prompt Integration**: Emit standard OSC prompt sequences (`\x1b]133;B`) for real-time remote/web front-end command tracking.
- **Interactive Chat**: Launch with `conn ai` for a collaborative troubleshooting session.


## Core Features
- **Multi-Protocol**: Native support for SSH, SFTP, Telnet, kubectl, Docker exec, and AWS SSM.
- **Context Management**: Set regex-based contexts to manage specific nodes across different environments (work, home, clients).
- **Advanced Inventory**:
    - Organize nodes in folders (`@folder`) and subfolders (`@subfolder@folder`).
    - Use Global Profiles (`@profilename`) to manage shared credentials easily.
    - Bulk creation, copying, moving, and export/import of nodes.
- **Modern UI**: High-performance terminal experience with `prompt-toolkit`, including:
    - Fuzzy search integration with `fzf`.
    - Advanced tab completion.
    - Syntax highlighting and customizable themes.
- **Automation Engine**: Run parallel tasks and playbooks on multiple devices with variable support.
- **Plugin System**: Build and execute custom Python scripts locally or on a remote gRPC server.
- **gRPC Architecture**: Fully decoupled Client/Server model for distributed management.
- **Privacy & Sync**: Local-first encrypted storage (RSA/OAEP) with optional Google Drive backup.


## Installation

```bash
pip install connpy
```

### Run it in Windows/Linux using Docker
```bash
git clone https://github.com/fluzzi/connpy
cd connpy
docker compose build

# Run it like a native app (completely silent)
docker compose run --rm --remove-orphans connpy-app [command]

# Pro Tip: Add this alias for a 100% native experience from any folder
alias conn='docker compose -f /path/to/connpy/docker-compose.yml run --rm --remove-orphans connpy-app'
```

---

## 🔒 Privacy & Integration

### Privacy Policy
Connpy is committed to protecting your privacy:
- **Local Storage**: All server addresses, usernames, and passwords are encrypted and stored **only** on your machine. No data is transmitted to our servers.
- **Data Access**: Data is used solely for managing and automating your connections.

### Google Integration
Used strictly for backup:
- **Backup**: Sync your encrypted configuration with your Google Drive account.
- **Scoped Access**: Connpy only accesses its own backup files.

---

## Usage

```bash
usage: conn [-h] [--add | --del | --mod | --show | --debug] [node|folder] [--sftp]
       conn {profile,move,copy,list,bulk,export,import,ai,run,api,plugin,config,sync,context} ...
```

### Basic Examples:
```bash
# Add a folder and subfolder
conn --add @office
conn --add @datacenter@office

# Add a node with a profile
conn --add server1@datacenter@office --profile @myuser

# Connect to a node (fuzzy match)
conn server1

# Start the AI Copilot
conn ai

# Run a command on all nodes in a folder
conn run @office "uptime"
```

---

## 🔌 Plugin System
Connpy supports a robust plugin architecture where scripts can run transparently on a remote gRPC server.

### Structure
Plugins must be Python files containing:
- **Class `Parser`**: Defines `argparse` arguments.
- **Class `Entrypoint`**: Execution logic.
- **Class `Preload`**: (Optional) Hooks and modifications to the core app.

See the [Plugin Requirements section](#plugin-requirements-for-connpy) for full technical details.

---

## Plugin Requirements for Connpy

### Remote Plugin Execution
When Connpy operates in remote mode, plugins are executed **transparently on the server**:
- The client automatically downloads the plugin source code (`Parser` class context) to generate the local `argparse` structure and provide autocompletion.
- The execution phase (`Entrypoint` class) is redirected via gRPC streams to execute in the server's memory.
- You can manage remote plugins using the `--remote` flag.

### General Structure
- The plugin script must define specific classes:
  1. **Class `Parser`**: Handles `argparse.ArgumentParser` initialization.
  2. **Class `Entrypoint`**: Main execution logic (receives `args`, `parser`, and `connapp`).
  3. **Class `Preload`**: (Optional) For modifying core app behavior or registering hooks.

### Preload Modifications and Hooks
You can customize the behavior of core classes using hooks:
- **`modify(method)`**: Alter class instances (e.g., `connapp.config`, `connapp.ai`).
- **`register_pre_hook(method)`**: Logic to run before a method execution.
- **`register_post_hook(method)`**: Logic to run after a method execution.

### Command Completion Support
Plugins can provide intelligent tab completion:
1. **Tree-based Completion (Recommended)**: Define `_connpy_tree(info)` returning a navigation dictionary.
2. **Legacy Completion**: Define `_connpy_completion(wordsnumber, words, info)`.

---

## ⚙️ gRPC Service Architecture
Connpy can operate in a decoupled mode:
1. **Start the API (Server)**: `conn api -s 50051`
2. **Configure the Client**:
   ```bash
   conn config --service-mode remote
   conn config --remote-host localhost:50051
   ```
All inventory management and execution will now happen on the server.

---

## 🐍 Automation Module (API)
You can use `connpy` as a Python library for your own scripts.

### Basic Execution
```python
import connpy
router = connpy.node("uniqueName", "1.1.1.1", user="admin")
router.run(["show ip int brief"])
print(router.output)
```

### Parallel Tasks with Variables
```python
import connpy
config = connpy.configfile()
nodes = config.getitem("@office", ["router1", "router2"])
routers = connpy.nodes(nodes, config=config)

variables = {
    "router1@office": {"id": "1"},
    "__global__": {"mask": "255.255.255.0"}
}
routers.run(["interface lo{id}", "ip address 10.0.0.{id} {mask}"], variables)
```

### AI Programmatic Use
```python
import connpy
myai = connpy.ai(connpy.configfile())
response = myai.ask("What is the status of the BGP neighbors in the office?")
```

---
*For detailed developer notes and plugin hooks documentation, see the [Documentation](https://fluzzi.github.io/connpy/).*

## 📜 License
[PolyForm Noncommercial 1.0.0](LICENSE)
