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
    token_re = re.compile(r'(\x1B(?:[\x30-\x5A\x5C-\x7E]|\[[0-?]*[ -/ ]*[@-~])|\r|\b|\x7f|[\x00-\x1F]|[^\x1B\r\b\x7f\x00-\x1F]+)')
    
    for line in lines:
        buffer = []
        cursor = 0
        
        for token in token_re.findall(line):
            if token == '\r':
                cursor = 0
            elif token in ('\b', '\x7f'):
                if cursor > 0:
                    cursor -= 1
            elif token.startswith('\x1B[') and len(token) >= 3:
                # Parse CSI: \x1B[ <params> <final_char>
                final = token[-1]
                param_str = token[2:-1]
                n = int(param_str) if param_str.isdigit() else 1

                if final == 'D':   # CUB – Cursor Back
                    cursor = max(0, cursor - n)
                elif final == 'C': # CUF – Cursor Forward
                    cursor = min(len(buffer), cursor + n)
                elif final == 'K': # EL  – Erase in Line
                    if n == 0 or param_str == '':      # Clear to end
                        buffer = buffer[:cursor]
                    elif n == 1:                         # Clear to start
                        buffer[:cursor] = [' '] * cursor
                    elif n == 2:                         # Clear entire line
                        buffer = []
                        cursor = 0
                elif final == 'G': # CHA – Cursor Horizontal Absolute (1-indexed)
                    cursor = max(0, n - 1)
                    # Pad buffer if cursor is beyond current length
                    if cursor > len(buffer):
                        buffer.extend([' '] * (cursor - len(buffer)))
                elif final == 'P': # DCH – Delete Characters
                    del buffer[cursor:cursor + n]
                elif final == '@': # ICH – Insert Characters
                    buffer[cursor:cursor] = [' '] * n
                # All other CSI sequences are silently discarded
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
