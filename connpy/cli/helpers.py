import os
import inquirer
try:
    from pyfzf.pyfzf import FzfPrompt
except ImportError:
    FzfPrompt = None

def get_config_dir():
    home = os.path.expanduser("~")
    defaultdir = os.path.join(home, '.config/conn')
    pathfile = os.path.join(defaultdir, '.folder')
    try:
        with open(pathfile, "r") as f:
            return f.read().strip()
    except:
        return defaultdir

def nodes_completer(prefix, parsed_args, **kwargs):
    configdir = get_config_dir()
    cache_file = os.path.join(configdir, '.fzf_nodes_cache.txt')
    if os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            return [line.strip() for line in f if line.startswith(prefix)]
    return []

def folders_completer(prefix, parsed_args, **kwargs):
    configdir = get_config_dir()
    cache_file = os.path.join(configdir, '.folders_cache.txt')
    if os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            return [line.strip() for line in f if line.startswith(prefix)]
    return []

def profiles_completer(prefix, parsed_args, **kwargs):
    configdir = get_config_dir()
    cache_file = os.path.join(configdir, '.profiles_cache.txt')
    if os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            return [line.strip() for line in f if line.startswith(prefix)]
    return []

def choose(app, list_, name, action):
    # Generates an inquirer list to pick
    # Safeguard: Never prompt if running in autocomplete shell
    if os.environ.get("_ARGCOMPLETE") or os.environ.get("COMP_LINE"):
        return None

    if FzfPrompt and app.fzf and os.environ.get("_ARGCOMPLETE") is None and os.environ.get("COMP_LINE") is None:
        fzf_prompt = FzfPrompt(executable_path="fzf-tmux")
        if not app.case:
            fzf_prompt = FzfPrompt(executable_path="fzf-tmux -i")
        answer = fzf_prompt.prompt(list_, fzf_options="-d 25%")
        if len(answer) == 0:
            return None
        else:
            return answer[0]
    else:
        questions = [inquirer.List(name, message="Pick {} to {}:".format(name,action), choices=list_, carousel=True)]
        answer = inquirer.prompt(questions)
        if answer == None:
            return None
        else:
            return answer[name]

def toplevel_completer(prefix, parsed_args, **kwargs):
    commands = ["node", "profile", "move", "mv", "copy", "cp", "list", "ls", "bulk", "export", "import", "ai", "run", "api", "context", "plugin", "config", "sync"]
    
    configdir = get_config_dir()
    cache_file = os.path.join(configdir, '.fzf_nodes_cache.txt')
    nodes = []
    if os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            nodes = [line.strip() for line in f if line.startswith(prefix)]
            
    cache_folders = os.path.join(configdir, '.folders_cache.txt')
    if os.path.exists(cache_folders):
        with open(cache_folders, "r") as f:
            nodes += [line.strip() for line in f if line.startswith(prefix)]
            
    return [c for c in commands + nodes if c.startswith(prefix)]
