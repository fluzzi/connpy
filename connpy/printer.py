import sys
from rich.console import Console
from rich.table import Table
from rich.live import Live

console = Console()
err_console = Console(stderr=True)


def _format_multiline(tag, message):
    message = str(message)
    lines = message.splitlines()
    if not lines:
        return f"\\[{tag}]"
    formatted = [f"\\[{tag}] {lines[0]}"]
    indent = " " * (len(tag) + 3)
    for line in lines[1:]:
        formatted.append(f"{indent}{line}")
    return "\n".join(formatted)

def info(message):
    console.print(_format_multiline("i", message))

def success(message):
    console.print(_format_multiline("✓", message))

def start(message):
    console.print(_format_multiline("+", message))

def warning(message):
    console.print(_format_multiline("!", message))

def error(message):
    # For error, we can create a temporary stderr console or just use the current one
    # err_console handles styles better than standard print and outputs to stderr.
    err_console.print(_format_multiline("✗", message), style="red")

def debug(message):
    console.print(_format_multiline("d", message))

def custom(tag, message):
    console.print(_format_multiline(tag, message))

def table(title, columns, rows, header_style="bold cyan", box=None):
    t = Table(title=title, header_style=header_style, box=box)
    for col in columns:
        t.add_column(col)
    for row in rows:
        t.add_row(*[str(item) for item in row])
    console.print(t)

