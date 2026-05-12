import re

def log_cleaner(data: str) -> str:
    """
    Stateless utility to remove ANSI sequences and process cursor movements.
    """
    if not data:
        return ""
            
    lines = data.split('\n')
    cleaned_lines = []
    
    # Regex to capture: ANSI sequences, control characters (\r, \b, etc), and plain text chunks
    token_re = re.compile(r'(\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/ ]*[@-~])|\r|\b|\x7f|[\x00-\x1F]|[^\x1B\r\b\x7f\x00-\x1F]+)')
    
    for line in lines:
        buffer = []
        cursor = 0
        
        for token in token_re.findall(line):
            if token == '\r':
                cursor = 0
            elif token in ('\b', '\x7f'):
                if cursor > 0:
                    cursor -= 1
            elif token == '\x1B[D': # Left Arrow
                if cursor > 0:
                    cursor -= 1
            elif token == '\x1B[C': # Right Arrow
                if cursor < len(buffer):
                    cursor += 1
            elif token == '\x1B[K': # Clear to end of line
                buffer = buffer[:cursor]
            elif token.startswith('\x1B'):
                continue
            elif len(token) == 1 and ord(token) < 32:
                continue
            else:
                for char in token:
                    if cursor == len(buffer):
                        buffer.append(char)
                    else:
                        buffer[cursor] = char
                    cursor += 1
        cleaned_lines.append("".join(buffer))
        
    return "\n".join(cleaned_lines).replace('\n\n', '\n').strip()
