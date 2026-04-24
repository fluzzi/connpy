import sys
import threading
import io

_local = threading.local()

class ThreadLocalStream:
    def __init__(self, original):
        self._original = original
    
    def _get_stream(self):
        s = getattr(_local, 'stream', None)
        return s if s is not None else self._original
    
    def write(self, data):
        stream = self._get_stream()
        if stream:
            stream.write(data)
    
    def flush(self):
        stream = self._get_stream()
        if stream:
            stream.flush()
    
    def isatty(self):
        stream = self._get_stream()
        return stream.isatty() if stream else False

    def __getattr__(self, name):
        # Avoid recursion during initialization or if _original is not yet set
        if name in ('_original', '_get_stream'):
            raise AttributeError(name)
        stream = self._get_stream()
        if stream:
            return getattr(stream, name)
        raise AttributeError(f"'NoneType' object has no attribute '{name}'")

# Patch stdout/stderr only once at module level
if not isinstance(sys.stdout, ThreadLocalStream):
    sys.stdout = ThreadLocalStream(sys.stdout)
if not isinstance(sys.stderr, ThreadLocalStream):
    sys.stderr = ThreadLocalStream(sys.stderr)

def _get_local():
    if not hasattr(_local, 'console'):
        _local.console = None
    if not hasattr(_local, 'err_console'):
        _local.err_console = None
    if not hasattr(_local, 'theme'):
        _local.theme = None
    return _local

def set_thread_stream(stream):
    if stream is None:
        if hasattr(_local, 'stream'):
            del _local.stream
    else:
        _local.stream = stream

def get_original_stdout():
    if isinstance(sys.stdout, ThreadLocalStream):
        return sys.stdout._original
    return sys.stdout

def get_original_stderr():
    if isinstance(sys.stderr, ThreadLocalStream):
        return sys.stderr._original
    return sys.stderr

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
    local = _get_local()
    
    # Self-healing patch: if sys.stdout was replaced (e.g. by pytest), re-wrap it.
    if not isinstance(sys.stdout, ThreadLocalStream):
        sys.stdout = ThreadLocalStream(sys.stdout)
        
    current_out = sys.stdout
    
    # Detect if we need to recreate the console (stream changed or closed)
    needs_recreate = (local.console is None or 
                     getattr(local, '_last_stdout', None) is not current_out)
    
    # Extra check for closed files in test environments
    if not needs_recreate and local.console is not None:
        try:
            if hasattr(local.console.file, 'closed') and local.console.file.closed:
                needs_recreate = True
        except Exception:
            pass

    if needs_recreate:
        from rich.console import Console
        from rich.theme import Theme
        if local.theme is None:
            local.theme = Theme(STYLES)
        local.console = Console(theme=local.theme, file=current_out)
        local._last_stdout = current_out
        
    return local.console

def _get_err_console():
    local = _get_local()
    
    # Self-healing patch for stderr
    if not isinstance(sys.stderr, ThreadLocalStream):
        sys.stderr = ThreadLocalStream(sys.stderr)
        
    current_err = sys.stderr
    
    needs_recreate = (local.err_console is None or 
                     getattr(local, '_last_stderr', None) is not current_err)
                     
    if not needs_recreate and local.err_console is not None:
        try:
            if hasattr(local.err_console.file, 'closed') and local.err_console.file.closed:
                needs_recreate = True
        except Exception:
            pass

    if needs_recreate:
        from rich.console import Console
        from rich.theme import Theme
        if local.theme is None:
            local.theme = Theme(STYLES)
        local.err_console = Console(stderr=True, theme=local.theme, file=current_err)
        local._last_stderr = current_err
        
    return local.err_console

def set_thread_console(console):
    _get_local().console = console

def set_thread_err_console(console):
    _get_local().err_console = console

def clear_thread_state():
    """Removes all thread-local printer state. Useful for gRPC thread reuse."""
    for attr in ["stream", "console", "err_console", "theme", "_last_stdout", "_last_stderr"]:
        if hasattr(_local, attr):
            delattr(_local, attr)

@property
def console():
    return _get_console()

@property
def err_console():
    return _get_err_console()

@property
def connpy_theme():
    local = _get_local()
    if local.theme is None:
        from rich.theme import Theme
        local.theme = Theme(STYLES)
    return local.theme

def apply_theme(user_styles=None):
    """
    Updates the global console themes with user-defined styles.
    If a style is missing in user_styles, it falls back to the default in STYLES.
    """
    local = _get_local()
    from rich.theme import Theme
    
    # Start with a copy of defaults
    active_styles = STYLES.copy()
    if user_styles:
        # Merge user styles (only if they are valid keys)
        for key, value in user_styles.items():
            if key in active_styles:
                active_styles[key] = value
                
    local.theme = Theme(active_styles)
    if local.console:
        local.console.push_theme(local.theme)
    if local.err_console:
        local.err_console.push_theme(local.theme)
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
    def __enter__(self):
        return _get_console().__enter__()
    def __exit__(self, exc_type, exc_val, exc_tb):
        return _get_console().__exit__(exc_type, exc_val, exc_tb)

class _ErrConsoleProxy:
    def __getattr__(self, name):
        return getattr(_get_err_console(), name)
    def __call__(self, *args, **kwargs):
        return _get_err_console()(*args, **kwargs)
    def __enter__(self):
        return _get_err_console().__enter__()
    def __exit__(self, exc_type, exc_val, exc_tb):
        return _get_err_console().__exit__(exc_type, exc_val, exc_tb)

console = _ConsoleProxy()
err_console = _ErrConsoleProxy()

# theme also needs to be lazy
class _ThemeProxy:
    def __getattr__(self, name):
        local = _get_local()
        if local.theme is None:
            from rich.theme import Theme
            local.theme = Theme(STYLES)
        return getattr(local.theme, name)

connpy_theme = _ThemeProxy()
