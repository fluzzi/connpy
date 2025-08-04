import sys

def _format_multiline(tag, message):
    lines = message.splitlines()
    if not lines:
        return f"[{tag}]"
    formatted = [f"[{tag}] {lines[0]}"]
    indent = " " * (len(tag) + 3)
    for line in lines[1:]:
        formatted.append(f"{indent}{line}")
    return "\n".join(formatted)

def info(message):
    print(_format_multiline("i", message))

def success(message):
    print(_format_multiline("✓", message))

def start(message):
    print(_format_multiline("+", message))

def warning(message):
    print(_format_multiline("!", message))

def error(message):
    print(_format_multiline("✗", message), file=sys.stderr)

def debug(message):
    print(_format_multiline("d", message))

def custom(tag, message):
    print(_format_multiline(tag, message))

