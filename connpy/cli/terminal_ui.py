import os
import re
import sys
import asyncio
import fcntl
import termios
import tty
from typing import Any, Dict, List, Optional, Callable
from textwrap import dedent

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.live import Live
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory

from ..printer import connpy_theme

def log_cleaner(data: str) -> str:
    """
    Stateless version of _logclean to remove ANSI sequences and process cursor movements.
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

class CopilotInterface:
    def __init__(self, config, history=None):
        self.config = config
        self.console = Console(theme=connpy_theme)
        self.history = history or InMemoryHistory()
        self.mode_range, self.mode_single, self.mode_lines = 0, 1, 2

    def extract_blocks(self, raw_bytes: bytes, cmd_byte_positions: List[tuple], node_info: dict) -> List[tuple]:
        """Identifies command blocks in the terminal history."""
        blocks = []
        if not (cmd_byte_positions and len(cmd_byte_positions) >= 2 and raw_bytes):
            return blocks
            
        default_prompt = r'>$|#$|\$$|>.$|#.$|\$.$'
        device_prompt = node_info.get("prompt", default_prompt) if isinstance(node_info, dict) else default_prompt
        prompt_re_str = re.sub(r'(?<!\\)\$', '', device_prompt)
        try:
            prompt_re = re.compile(prompt_re_str)
        except Exception:
            prompt_re = re.compile(re.sub(r'(?<!\\)\$', '', default_prompt))
            
        for i in range(1, len(cmd_byte_positions)):
            pos, known_cmd = cmd_byte_positions[i]
            prev_pos = cmd_byte_positions[i-1][0]
            
            if known_cmd:
                prev_chunk = raw_bytes[prev_pos:pos]
                prev_cleaned = log_cleaner(prev_chunk.decode(errors='replace'))
                prev_lines = [l for l in prev_cleaned.split('\n') if l.strip()]
                prompt_text = prev_lines[-1].strip() if prev_lines else ""
                preview = f"{prompt_text}{known_cmd}" if prompt_text else known_cmd
                blocks.append((pos, preview[:80]))
            else:
                chunk = raw_bytes[prev_pos:pos]
                cleaned = log_cleaner(chunk.decode(errors='replace'))
                lines = [l for l in cleaned.split('\n') if l.strip()]
                preview = lines[-1].strip() if lines else ""
                
                if preview:
                    match = prompt_re.search(preview)
                    if match:
                        cmd_text = preview[match.end():].strip()
                        if cmd_text:
                            blocks.append((pos, preview[:80]))
        return blocks

    async def run_session(self, 
                          raw_bytes: bytes, 
                          cmd_byte_positions: List[tuple], 
                          node_info: dict,
                          on_ai_call: Callable):
        """
        Runs the interactive Copilot session.
        on_ai_call: async function(active_buffer, question) -> result_dict
        """
        from rich.rule import Rule
        
        try:
            # Prepare UI state
            buffer = log_cleaner(raw_bytes.decode(errors='replace'))
            blocks = self.extract_blocks(raw_bytes, cmd_byte_positions, node_info)
            last_line = buffer.split('\n')[-1].strip() if buffer.strip() else "(prompt)"
            blocks.append((len(raw_bytes), last_line[:80]))
            
            state = {
                'context_cmd': 1,
                'total_cmds': len(blocks),
                'total_lines': len(buffer.split('\n')),
                'context_lines': min(50, len(buffer.split('\n'))),
                'context_mode': self.mode_range,
                'cancelled': False
            }
            
            # 1. Visual Separation
            self.console.print("") # Salto de línea real
            self.console.print(Rule(title="[bold cyan] AI TERMINAL COPILOT [/bold cyan]", style="cyan"))
            self.console.print(Panel(
                "[dim]Type your question. Enter to send, Escape/Ctrl+C to cancel.\n"
                "Tab to change context mode. Ctrl+\u2191/\u2193 to adjust context. \u2191\u2193 for question history.[/dim]",
                border_style="cyan"
            ))
            self.console.print("\n") # Pequeño espacio antes del prompt del copilot

            bindings = KeyBindings()
            @bindings.add('c-up')
            def _(event):
                if state['context_mode'] == self.mode_lines:
                    state['context_lines'] = min(state['context_lines'] + 50, state['total_lines'])
                else:
                    state['context_cmd'] = min(state['context_cmd'] + 1, state['total_cmds'])
                event.app.invalidate()
            @bindings.add('c-down')
            def _(event):
                if state['context_mode'] == self.mode_lines:
                    state['context_lines'] = max(state['context_lines'] - 50, min(50, state['total_lines']))
                else:
                    state['context_cmd'] = max(state['context_cmd'] - 1, 1)
                event.app.invalidate()
            @bindings.add('tab')
            def _(event):
                state['context_mode'] = (state['context_mode'] + 1) % 3
                event.app.invalidate()
            @bindings.add('escape', eager=True)
            @bindings.add('c-c')
            def _(event):
                state['cancelled'] = True
                event.app.exit(result='')

            def get_active_buffer():
                if state['context_mode'] == self.mode_lines:
                    return '\n'.join(buffer.split('\n')[-state['context_lines']:])
                idx = max(0, state['total_cmds'] - state['context_cmd'])
                start, preview = blocks[idx]
                if state['context_mode'] == self.mode_single and idx + 1 < state['total_cmds']:
                    end = blocks[idx + 1][0]
                    active_raw = raw_bytes[start:end]
                else:
                    active_raw = raw_bytes[start:]
                return preview + "\n" + log_cleaner(active_raw.decode(errors='replace'))

            def get_prompt_text():
                if state['context_mode'] == self.mode_lines:
                    return HTML(f"<ansicyan>Ask [Ctx: {state['context_lines']}/{state['total_lines']}L]: </ansicyan>")
                active = get_active_buffer()
                lines_count = len(active.split('\n'))
                mode_str = {self.mode_range: "Range", self.mode_single: "Cmd"}[state['context_mode']]
                return HTML(f"<ansicyan>Ask [{mode_str} {state['context_cmd']} ~{lines_count}L]: </ansicyan>")

            def get_toolbar():
                m_label = {self.mode_range: "RANGE", self.mode_single: "SINGLE", self.mode_lines: "LINES"}[state['context_mode']]
                if state['context_mode'] == self.mode_lines:
                    return HTML(f"<ansigray>\u25b6 Ctrl+\u2191/\u2193 adjusts by 50 lines  [Tab: {m_label}]</ansigray>")
                idx = max(0, state['total_cmds'] - state['context_cmd'])
                return HTML(f"<ansigray>\u25b6 {blocks[idx][1]}  [Tab: {m_label}]</ansigray>")

            # 2. Ask question
            session = PromptSession(history=self.history)
            try:
                # Usamos un try/finally interno para asegurar que si algo falla en prompt_async,
                # no nos quedemos con la terminal en un estado extraño.
                question = await session.prompt_async(
                    get_prompt_text, 
                    key_bindings=bindings, 
                    bottom_toolbar=get_toolbar
                )
            except (KeyboardInterrupt, EOFError):
                state['cancelled'] = True
                question = ""
            
            if state['cancelled'] or not question.strip() or question.strip().lower() == 'cancel':
                return "cancel", None, None

            # Enrich question
            past = self.history.get_strings()
            if len(past) > 1:
                history_text = "\n".join(f"- {q}" for q in past[-6:-1])
                question = f"Previous questions:\n{history_text}\n\nCurrent Question:\n{question}"

            # 3. AI Execution
            active_buffer = get_active_buffer()
            live_text = "Thinking..."
            panel = Panel(live_text, title="[bold cyan]Copilot Guide[/bold cyan]", border_style="cyan")
            
            def on_chunk(text):
                nonlocal live_text
                if live_text == "Thinking...": live_text = ""
                live_text += text
            
            with Live(panel, console=self.console, refresh_per_second=10) as live:
                def update_live(t):
                    live.update(Panel(Markdown(t), title="[bold cyan]Copilot Guide[/bold cyan]", border_style="cyan"))

                wrapped_chunk = lambda t: (on_chunk(t), update_live(live_text))
                
                # Check for interruption during AI call
                ai_task = asyncio.create_task(on_ai_call(active_buffer, question, wrapped_chunk))
                
                try:
                    while not ai_task.done():
                        await asyncio.sleep(0.05)
                    result = await ai_task
                except asyncio.CancelledError:
                    return "cancel", None, None

            if not result or result.get("error"):
                if result and result.get("error"): self.console.print(f"[red]Error: {result['error']}[/red]")
                return "cancel", None, None

            # 4. Handle result
            if live_text == "Thinking..." and result.get("guide"):
                self.console.print(Panel(Markdown(result["guide"]), title="[bold cyan]Copilot Guide[/bold cyan]", border_style="cyan"))

            commands = result.get("commands", [])
            if not commands:
                return "cancel", None, None

            risk = result.get("risk_level", "low")
            style = {"low": "green", "high": "yellow", "destructive": "red"}.get(risk, "green")
            cmd_text = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(commands))
            self.console.print(Panel(cmd_text, title=f"[bold {style}]Suggested Commands [{risk.upper()}][/bold {style}]", border_style=style))

            confirm_session = PromptSession()
            c_bindings = KeyBindings()
            @c_bindings.add('escape', eager=True)
            @c_bindings.add('c-c')
            def _(ev): ev.app.exit(result='n')
            
            try:
                action = await confirm_session.prompt_async(HTML(f"<ansi{style}>Send? (y/n/e/number) [n]: </ansi{style}>"), key_bindings=c_bindings)
            except (KeyboardInterrupt, EOFError):
                action = "n"

            action_l = (action or "n").lower().strip()
            if action_l in ('y', 'yes', 'all'):
                return "send_all", commands, None
            elif action_l.startswith('e'):
                target = "\n".join(commands)
                e_bindings = KeyBindings()
                @e_bindings.add('c-j')
                def _(ev): ev.app.exit(result=ev.app.current_buffer.text)
                @e_bindings.add('escape', 'enter')
                def _(ev): ev.app.exit(result=ev.app.current_buffer.text)
                @e_bindings.add('escape')
                def _(ev): ev.app.exit(result='')
                
                edited = await confirm_session.prompt_async(
                    HTML("<ansicyan>Edit (Ctrl+Enter or Esc+Enter to submit):\n</ansicyan>"),
                    default=target, multiline=True, key_bindings=e_bindings
                )
                if edited.strip():
                    # Split by lines to ensure core.py applies delay between each command
                    lines = [l.strip() for l in edited.split('\n') if l.strip()]
                    return "custom", None, lines
                return "cancel", None, None
            
            return "cancel", None, None

        finally:
            self.console.print("[dim]Returning to session...[/dim]")

