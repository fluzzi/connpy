import os

def get_help(type, parsers=None):
    if type == "export":
        return "Export /path/to/file.yml \[@subfolder1]\[@folder1] \[@subfolderN]\[@folderN]"
    if type == "import":
        return "Import /path/to/file.yml"
    if type == "node":
        return "node\[@subfolder]\[@folder]\nConnect to specific node or show all matching nodes\n\[@subfolder]\[@folder]\nShow all available connections globally or in specified path"
    if type == "usage":
        commands = []
        for subcommand, subparser in parsers.choices.items():
            if subparser.description != None:
                commands.append(subcommand)
        commands = ",".join(commands)
        usage_help = f"connpy [-h] [--add | --del | --mod | --show | --debug] [node|folder] [--sftp]\n       connpy {{{commands}}} ..."
        return usage_help
    return get_instructions(type)

def get_instructions(type="add"):
    if type == "add":
        return """
Welcome to Connpy node Addition Wizard!

Here are some important instructions and tips for configuring your new node:

1. **Profiles**:
   - You can use the configured settings in a profile using `@profilename`.

2. **Available Protocols and Apps**:
   - ssh
   - telnet
   - kubectl (`kubectl exec`)
   - docker (`docker exec`)

3. **Optional Values**:
   - You can leave any value empty except for the hostname/IP.

4. **Passwords**:
   - You can pass one or more passwords using comma-separated `@profiles`.

5. **Logging**:
   - You can use the following variables in the logging file name:
     - `${id}`
     - `${unique}`
     - `${host}`
     - `${port}`
     - `${user}`
     - `${protocol}`

6. **Well-Known Tags**:
   - `os`: Identified by AI to generate commands based on the operating system.
   - `screen_length_command`: Used by automation to avoid pagination on different devices (e.g., `terminal length 0` for Cisco devices).
   - `prompt`: Replaces default app prompt to identify the end of output or where the user can start inputting commands.
   - `kube_command`: Replaces the default command (`/bin/bash`) for `kubectl exec`.
   - `docker_command`: Replaces the default command for `docker exec`.
"""
    if type == "bashcompletion":
        return '''
# Bash completion for connpy
# Run: eval "$(connpy config --completion bash)"
# Or add it to your .bashrc

_connpy_autocomplete()
{
  local strings
  strings=$(python3 -m connpy.completion bash ${#COMP_WORDS[@]} "${COMP_WORDS[@]}")
  
  local IFS=$'\\t'
  COMPREPLY=( $(compgen -W "$strings" -- "${COMP_WORDS[$COMP_CWORD]}") )
}
complete -o nosort -F _connpy_autocomplete conn
complete -o nosort -F _connpy_autocomplete connpy
'''
    if type == "zshcompletion":
        return '''
# Zsh completion for connpy
# Run: eval "$(connpy config --completion zsh)"
# Or add it to your .zshrc
# Make sure compinit is loaded

autoload -U compinit && compinit
_connpy_autocomplete()
{
    local COMP_WORDS num strings
    COMP_WORDS=( $words )
    num=${#COMP_WORDS[@]}
    if [[ $words =~ '.* $' ]]; then
        num=$(($num + 1))
    fi
    strings=$(python3 -m connpy.completion zsh ${num} ${COMP_WORDS[@]})
    
    local IFS=$'\\t'
    compadd "$@" -- ${=strings}
}
compdef _connpy_autocomplete conn
compdef _connpy_autocomplete connpy
'''
    if type == "fzf_wrapper_bash":
        return '''\n#Here starts bash 0ms fzf wrapper for connpy
connpy() {
    if [ $# -eq 0 ]; then
        local selected
        local configdir=$(cat ~/.config/conn/.folder 2>/dev/null || echo ~/.config/conn)
        if [ -s "$configdir/.fzf_nodes_cache.txt" ]; then
            selected=$(cat "$configdir/.fzf_nodes_cache.txt" | fzf-tmux -i -d 25%)
        else
            command connpy
            return
        fi
        if [ -n "$selected" ]; then
            command connpy "$selected"
        fi
    else
        command connpy "$@"
    fi
}
alias c="connpy"
#Here ends bash 0ms fzf wrapper for connpy
'''
    if type == "fzf_wrapper_zsh":
        return '''\n#Here starts zsh 0ms fzf wrapper for connpy
connpy() {
    if [ $# -eq 0 ]; then
        local selected
        local configdir=$(cat ~/.config/conn/.folder 2>/dev/null || echo ~/.config/conn)
        if [ -s "$configdir/.fzf_nodes_cache.txt" ]; then
            selected=$(cat "$configdir/.fzf_nodes_cache.txt" | fzf-tmux -i -d 25%)
        else
            command connpy
            return
        fi
        if [ -n "$selected" ]; then
            command connpy "$selected"
        fi
    else
        command connpy "$@"
    fi
}
alias c="connpy"
#Here ends zsh 0ms fzf wrapper for connpy
'''
    if type == "run":
        return "node[@subfolder][@folder] commmand to run\nRun the specific command on the node and print output\n/path/to/file.yaml\nUse a yaml file to run an automation script"
    if type == "generate":
        return r'''---
tasks:
- name: "Config"

  action: 'run' #Action can be test or run. Mandatory

  nodes: #List of nodes to work on. Mandatory
  - 'router1@office' #You can add specific nodes
  - '@aws'  #entire folders or subfolders
  - '@office':   #or filter inside a folder or subfolder
    - 'router2'
    - 'router7'

  commands: #List of commands to send, use {name} to pass variables
  - 'term len 0'
  - 'conf t'
  - 'interface {if}'
  - 'ip address 10.100.100.{id} 255.255.255.255'
  - '{commit}'
  - 'end'

  variables: #Variables to use on commands and expected. Optional
    __global__: #Global variables to use on all nodes, fallback if missing in the node.
      commit: ''
      if: 'loopback100'
    router1@office:
      id: 1
    router2@office:
      id: 2
      commit: 'commit'
    router3@office:
      id: 3
    vrouter1@aws:
      id: 4
    vrouterN@aws:
      id: 5
  
  output: /home/user/logs #Type of output, if null you only get Connection and test result. Choices are: null,stdout,/path/to/folder. Folder path only works on 'run' action.
  
  options:
    prompt: r'>$|#$|\$$|>.$|#.$|\$.$' #Optional prompt to check on your devices, default should work on most devices.
    parallel: 10 #Optional number of nodes to run commands on parallel. Default 10.
    timeout: 20 #Optional time to wait in seconds for prompt, expected or EOF. Default 20. 

- name: "TestConfig"
  action: 'test'
  nodes:
  - 'router1@office'
  - '@aws'
  - '@office':
    - 'router2'
    - 'router7'
  commands:
  - 'ping 10.100.100.{id}'
  expected: '!' #Expected text to find when running test action. Mandatory for 'test'
  variables:
    router1@office:
      id: 1
    router2@office:
      id: 2
      commit: 'commit'
    router3@office:
      id: 3
    vrouter1@aws:
      id: 4
    vrouterN@aws:
      id: 5
  output: null
...'''
    return ""
