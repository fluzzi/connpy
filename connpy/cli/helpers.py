import os
import inquirer
from inquirer.themes import Default, term

try:
    from pyfzf.pyfzf import FzfPrompt
except ImportError:
    FzfPrompt = None

def hex_to_blessed(hex_str):
    """Convert hex color string to blessed/ansi format."""
    if not hex_str or not isinstance(hex_str, str):
        return term.normal
    
    # Check for bold prefix
    prefix = ""
    if hex_str.startswith('bold '):
        prefix = term.bold
        hex_str = hex_str.replace('bold ', '').strip()
    
    # If it's a standard color name
    if not hex_str.startswith('#'):
        return prefix + getattr(term, hex_str, term.normal)
    
    # Parse hex
    try:
        h = hex_str.lstrip('#')
        if len(h) == 3:
            h = ''.join([c*2 for c in h])
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        
        # Try RGB, fallback to standard cyan if it fails or returns empty
        try:
            c = term.color_rgb(r, g, b)
            if not c: # Some terms return empty for RGB
                return prefix + term.cyan
            return prefix + c
        except:
            return prefix + term.cyan
    except:
        return prefix + term.normal

# Custom inquirer theme matching connpy colors
class ConnpyTheme(Default):
    def __init__(self):
        super().__init__()
        try:
            from ..printer import _global_active_styles
            # Use user_prompt as primary accent, fallback to info/cyan
            accent = _global_active_styles.get("user_prompt", _global_active_styles.get("info", "cyan"))
            accent_color = hex_to_blessed(accent)
            
            self.Question.mark_color = accent_color
            self.List.selection_color = accent_color
            self.List.selection_cursor = ">"
        except:
            # Absolute fallback to standard cyan
            self.Question.mark_color = term.cyan
            self.List.selection_color = term.bold_cyan
            self.List.selection_cursor = ">"

def get_theme():
    """Returns a fresh instance of the theme with current colors."""
    return ConnpyTheme()

class ThemeProxy:
    """Proxy to ensure theme colors are resolved at runtime."""
    def __getattr__(self, name):
        return getattr(get_theme(), name)
    def __iter__(self):
        return iter(get_theme())
    def __getitem__(self, item):
        return get_theme()[item]

theme = ThemeProxy()

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
        answer = inquirer.prompt(questions, theme=theme)
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
