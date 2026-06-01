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
            import time
            retries = 0
            while True:
                try:
                    stream.write(data)
                    break
                except BlockingIOError:
                    if retries > 50:
                        raise
                    time.sleep(0.01)
                    retries += 1
    
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
    if not hasattr(_local, 'theme') or _local.theme is None:
        from rich.theme import Theme
        _local.theme = Theme(_global_active_styles)
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
    "info": "#00ffff",        # Cyan
    "warning": "#ffff00",     # Yellow
    "error": "#ff0000",       # Red
    "success": "#00ff00",     # Green
    "debug": "#888888",
    "header": "bold #00ffff",
    "key": "bold #00ffff",
    "border": "#00ffff",
    "pass": "bold #00ff00",
    "fail": "bold #ff0000",
    "engineer": "#5fafff",    # Sky Blue (lighter than pure blue)
    "architect": "#9370db",   # Medium Purple
    "ai_status": "bold #00ff00",
    "user_prompt": "bold #00afd7", # Deep Sky Blue / Soft Cyan
    "unavailable": "#d78700",
    "contrast": "#bbbbbb",
}

LIGHT_THEME = {
    "info": "#00008b",        # Navy Blue
    "warning": "#d78700",     # Orange
    "error": "#cd0000",       # Dark Red
    "success": "#006400",     # Dark Green
    "debug": "#777777",
    "header": "bold #00008b",
    "key": "bold #00008b",
    "border": "#00008b",
    "pass": "bold #006400",
    "fail": "bold #cd0000",
    "engineer": "#00008b",
    "architect": "#8b008b",   # Dark Magenta
    "ai_status": "bold #006400",
    "user_prompt": "bold #00008b",
    "unavailable": "#666666",
    "contrast": "#777777",
}

_global_active_styles = STYLES.copy()

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
        local.theme = Theme(_global_active_styles)
    return local.theme

def apply_theme(user_styles=None):
    """
    Updates the global console themes with user-defined styles.
    If a style is missing in user_styles, it falls back to the default in STYLES.
    """
    global _global_active_styles
    local = _get_local()
    from rich.theme import Theme
    
    # Start with a copy of defaults
    active_styles = STYLES.copy()
    if user_styles:
        # Merge user styles (only if they are valid keys)
        for key, value in user_styles.items():
            if key in active_styles:
                active_styles[key] = value
                
    _global_active_styles = active_styles
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
        if style:
            return f"[{style}]\\[{tag}][/{style}]"
        return f"\\[{tag}]"
    
    # Apply style to the tag if provided
    styled_tag = f"[{style}]\\[{tag}][/{style}]" if style else f"\\[{tag}]"
    if style:
        # Include brackets in the styling
        styled_tag = f"[{style}]\\[{tag}][/{style}]"
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
    """Print an aggregate summary of multiple test results in a single panel."""
    from rich.panel import Panel
    from rich.text import Text
    from rich.console import Group
    import os
    
    try:
        cols, _ = os.get_terminal_size()
    except OSError:
        cols = 80

    summary_content = Text()
    total_passed = 0
    total_failed = 0
    total_partial = 0

    if not results:
        summary_content.append("  No test results found.\n", style="error")
    else:
        for node, test_result in results.items():
            summary_content.append(f"• ", style="border")
            summary_content.append(f"{node.ljust(40)}", style="bold")
            
            if test_result:
                passed_count = sum(1 for v in test_result.values() if v)
                total_count = len(test_result)
                
                if passed_count == total_count:
                    total_passed += 1
                    node_style = "success"
                    mark = "✓ PASS"
                elif passed_count > 0:
                    total_partial += 1
                    node_style = "warning"
                    mark = f"⚠ PARTIAL ({passed_count}/{total_count})"
                else:
                    total_failed += 1
                    node_style = "error"
                    mark = "✗ FAIL"
                
                summary_content.append(f" {mark}\n", style=node_style)
                for k, v in test_result.items():
                    res_mark = "✓" if v else "✗"
                    res_style = "success" if v else "error"
                    summary_content.append(f"    {k.ljust(38)} {res_mark}\n", style=res_style)
            else:
                total_failed += 1
                summary_content.append(" ✗ FAIL\n", style="error")
                summary_content.append("    No results (execution failed)\n", style="error")

    status_parts = []
    if total_passed: status_parts.append(f"[pass]{total_passed} PASSED[/pass]")
    if total_partial: status_parts.append(f"[warning]{total_partial} PARTIAL[/warning]")
    if total_failed: status_parts.append(f"[fail]{total_failed} FAILED[/fail]")
    
    status_str = " | ".join(status_parts) if status_parts else "[error]NO RESULTS[/error]"
    title_line = f"AGGREGATE TEST SUMMARY — {status_str}"
    
    _get_console().print(Panel(Group(Text(), summary_content), title=title_line, width=cols, border_style="border"))

def run_summary(results):
    """Print an aggregate summary of multiple execution results in a single panel."""
    from rich.panel import Panel
    from rich.text import Text
    from rich.console import Group
    import os
    
    try:
        cols, _ = os.get_terminal_size()
    except OSError:
        cols = 80

    summary_content = Text()
    total_ok = 0
    total_err = 0

    if not results:
        summary_content.append("  No execution results found.\n", style="error")
    else:
        for node, data in results.items():
            summary_content.append(f"• ", style="border")
            summary_content.append(f"{node.ljust(40)}", style="bold")
            
            # Check if we have a status dict or just output (for backward compatibility)
            status = data.get("status", 0) if isinstance(data, dict) else 0
            
            if status == 0:
                total_ok += 1
                summary_content.append(f" ✓ DONE\n", style="success")
            else:
                total_err += 1
                summary_content.append(f" ✗ FAIL({status})\n", style="error")

    status_parts = []
    if total_ok: status_parts.append(f"[success]{total_ok} DONE[/success]")
    if total_err: status_parts.append(f"[error]{total_err} FAILED[/error]")
    
    status_str = " | ".join(status_parts) if status_parts else "[error]NO RESULTS[/error]"
    title_line = f"AGGREGATE EXECUTION SUMMARY — {status_str}"
    
    _get_console().print(Panel(Group(Text(), summary_content), title=title_line, width=cols, border_style="border"))

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
            local.theme = Theme(_global_active_styles)
        return getattr(local.theme, name)

connpy_theme = _ThemeProxy()

class BlockMarkdownRenderer:
    """
    Block-buffered streaming markdown renderer.
    Accumulates text until block boundaries are detected,
    then renders complete blocks using Rich's Markdown.
    """
    def __init__(self, console=None):
        from rich.console import Console as RichConsole
        from .printer import connpy_theme, get_original_stdout
        self._console = console or RichConsole(
            theme=connpy_theme, file=get_original_stdout()
        )
        self._line_buf = ""        # chars waiting for \n
        self._block_lines = []     # complete lines for current block
        self._in_code_block = False

    def feed(self, text):
        self._line_buf += text
        while '\n' in self._line_buf:
            idx = self._line_buf.index('\n')
            line = self._line_buf[:idx + 1]
            self._line_buf = self._line_buf[idx + 1:]
            self._process_line(line)

    def flush(self):
        if self._line_buf:
            self._block_lines.append(self._line_buf)
            self._line_buf = ""
        self._flush_block()

    def _process_line(self, line):
        stripped = line.strip()
        
        if stripped.startswith('```'):
            if not self._in_code_block:
                # Flush accumulated text before code block
                self._flush_block()
                self._in_code_block = True
                self._block_lines.append(line)
            else:
                # Include closing fence and flush code block
                self._block_lines.append(line)
                self._in_code_block = False
                self._flush_block()
            return

        if self._in_code_block:
            self._block_lines.append(line)
            return

        # Blank line = paragraph break
        if stripped == '':
            self._block_lines.append(line)
            self._flush_block()
            return

        self._block_lines.append(line)

    def _flush_block(self):
        if not self._block_lines:
            return
        block_text = ''.join(self._block_lines).strip()
        self._block_lines = []
        if not block_text:
            return
        from rich.markdown import Markdown
        self._console.print(Markdown(block_text, code_theme="ansi_dark"))

# Alias for backward compatibility
IncrementalMarkdownParser = BlockMarkdownRenderer
