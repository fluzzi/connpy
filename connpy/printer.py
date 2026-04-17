# Lazy-loaded printer module to speed up CLI startup
_console = None
_err_console = None
_theme = None

# Centralized design system
STYLES = {
    "info": "cyan",
    "warning": "yellow",
    "error": "red",
    "success": "green",
    "debug": "dim",
    "header": "bold cyan",
    "key": "bold cyan",
    "border": "cyan",
    "pass": "bold green",
    "fail": "bold red",
    "engineer": "blue",
    "architect": "medium_purple",
    "ai_status": "bold green",
    "user_prompt": "bold cyan",
    "unavailable": "orange3",
}

def _get_console():
    global _console, _theme
    if _console is None:
        from rich.console import Console
        from rich.theme import Theme
        if _theme is None:
            _theme = Theme(STYLES)
        _console = Console(theme=_theme)
    return _console

def _get_err_console():
    global _err_console, _theme
    if _err_console is None:
        from rich.console import Console
        from rich.theme import Theme
        if _theme is None:
            _theme = Theme(STYLES)
        _err_console = Console(stderr=True, theme=_theme)
    return _err_console

@property
def console():
    return _get_console()

@property
def err_console():
    return _get_err_console()

@property
def connpy_theme():
    global _theme
    if _theme is None:
        from rich.theme import Theme
        _theme = Theme(STYLES)
    return _theme

def apply_theme(user_styles=None):
    """
    Updates the global console themes with user-defined styles.
    If a style is missing in user_styles, it falls back to the default in STYLES.
    """
    global _theme, _console, _err_console
    from rich.theme import Theme
    
    # Start with a copy of defaults
    active_styles = STYLES.copy()
    if user_styles:
        # Merge user styles (only if they are valid keys)
        for key, value in user_styles.items():
            if key in active_styles:
                active_styles[key] = value
                
    _theme = Theme(active_styles)
    if _console:
        _console.push_theme(_theme)
    if _err_console:
        _err_console.push_theme(_theme)
    return active_styles


def _format_multiline(tag, message, style=None):
    message = str(message)
    lines = message.splitlines()
    if not lines:
        return f"[{style}]\\[{tag}][/{style}]" if style else f"\\[{tag}]"
    
    # Apply style to the tag if provided
    styled_tag = f"[{style}]\\[{tag}][/{style}]" if style else f"\\[{tag}]"
    formatted = [f"{styled_tag} {lines[0]}"]
    
    # Indent subsequent lines
    indent = " " * (len(tag) + 3)
    for line in lines[1:]:
        formatted.append(f"{indent}{line}")
    return "\n".join(formatted)

def info(message):
    _get_console().print(_format_multiline("i", message, style="info"))

def success(message):
    _get_console().print(_format_multiline("✓", message, style="success"))

def start(message):
    _get_console().print(_format_multiline("+", message, style="success"))

def warning(message):
    _get_console().print(_format_multiline("!", message, style="warning"))

def error(message):
    _get_err_console().print(_format_multiline("✗", message, style="error"))

def debug(message):
    _get_console().print(_format_multiline("d", message, style="debug"))

def custom(tag, message):
    _get_console().print(_format_multiline(tag, message, style="header"))

def table(title, columns, rows, header_style="header", box=None):
    from rich.table import Table
    t = Table(title=title, header_style=header_style, box=box)
    for col in columns:
        t.add_column(col)
    for row in rows:
        t.add_row(*[str(item) for item in row])
    _get_console().print(t)

def data(title, content, language="yaml"):
    """Display structured data with syntax highlighting inside a panel."""
    from rich.syntax import Syntax
    from rich.panel import Panel
    syntax = Syntax(content, language, theme="ansi_dark", word_wrap=True, background_color="default")
    panel = Panel(syntax, title=f"[header]{title}[/header]", border_style="border", expand=False)
    _get_console().print(panel)

def node_panel(unique, output, status, title_prefix=""):
    """Display node execution result in a styled panel."""
    from rich.panel import Panel
    from rich.text import Text
    from rich.console import Group
    import os
    
    try:
        cols, _ = os.get_terminal_size()
    except OSError:
        cols = 80

    if status == 0:
        status_str = "[pass]✓ PASS[/pass]"
        border = "pass"
    else:
        status_str = f"[fail]✗ FAIL({status})[/fail]"
        border = "fail"
        
    title_line = f"{title_prefix}[bold]{unique}[/bold] — {status_str}"
    stripped = output.strip() if output else ""
    code_block = Text(stripped + "\n") if stripped else Text()
    
    _get_console().print(Panel(Group(Text(), code_block), title=title_line, width=cols, border_style=border))

def test_panel(unique, output, status, result):
    """Display test execution result in a styled panel."""
    from rich.panel import Panel
    from rich.text import Text
    from rich.console import Group
    import os
    
    try:
        cols, _ = os.get_terminal_size()
    except OSError:
        cols = 80

    is_pass = (status == 0 and result and all(result.values()))
    
    if is_pass:
        status_str = "[pass]✓ PASS[/pass]"
        border = "pass"
    else:
        status_str = f"[fail]✗ FAIL[/fail]"
        border = "fail"
        
    title_line = f"[bold]{unique}[/bold] — {status_str}"
    
    stripped = output.strip() if output else ""
    code_block = Text(stripped + "\n") if stripped else Text()
    
    test_results = Text()
    test_results.append("\nTEST RESULTS:\n", style="header")
    if result:
        max_key_len = max(len(k) for k in result.keys())
        for k, v in result.items():
            mark = "✓" if v else "✗"
            style = "success" if v else "error"
            test_results.append(f"  {k.ljust(max_key_len)}  {mark}\n", style=style)
    else:
        test_results.append("  No results (execution failed)\n", style="error")
            
    _get_console().print(Panel(Group(Text(), code_block, test_results), title=title_line, width=cols, border_style=border))

def test_summary(results):
    """Print an aggregate summary of multiple test results."""
    from rich.panel import Panel
    from rich.text import Text
    from rich.console import Group
    import os
    
    try:
        cols, _ = os.get_terminal_size()
    except OSError:
        cols = 80

    for node, test_result in results.items():
        status_code = 0 if test_result and all(test_result.values()) else 1
        if status_code == 0:
            status_str = "[pass]✓ PASS[/pass]"
            border = "pass"
        else:
            status_str = f"[fail]✗ FAIL[/fail]"
            border = "fail"
            
        title_line = f"[bold]{node}[/bold] — {status_str}"
        
        test_output = Text()
        test_output.append("TEST RESULTS:\n", style="header")
        max_key_len = max(len(k) for k in test_result.keys()) if test_result else 0
        for k, v in (test_result.items() if test_result else []):
            mark = "✓" if v else "✗"
            style = "success" if v else "error"
            test_output.append(f"  {k.ljust(max_key_len)}  {mark}\n", style=style)
            
        _get_console().print(Panel(Group(Text(), test_output), title=title_line, width=cols, border_style=border))

def header(text):
    """Print a section header."""
    from rich.rule import Rule
    _get_console().print(Rule(text, style="header"))

def kv(key, value):
    """Print an inline key-value pair."""
    _get_console().print(f"[key]{key}[/key]: {value}")

def confirm_action(item, action):
    """Print a confirmation pre-action message."""
    _get_console().print(f"\\[i] [bold]{action}[/bold]: {item}", style="info")

# Compatibility proxies
class _ConsoleProxy:
    def __getattr__(self, name):
        return getattr(_get_console(), name)
    def __call__(self, *args, **kwargs):
        return _get_console()(*args, **kwargs)

class _ErrConsoleProxy:
    def __getattr__(self, name):
        return getattr(_get_err_console(), name)
    def __call__(self, *args, **kwargs):
        return _get_err_console()(*args, **kwargs)

console = _ConsoleProxy()
err_console = _ErrConsoleProxy()

# theme also needs to be lazy
class _ThemeProxy:
    def __getattr__(self, name):
        global _theme
        if _theme is None:
            from rich.theme import Theme
            _theme = Theme(STYLES)
        return getattr(_theme, name)

connpy_theme = _ThemeProxy()
