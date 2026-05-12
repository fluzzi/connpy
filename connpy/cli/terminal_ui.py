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
from connpy.utils import log_cleaner
from ..services.ai_service import AIService

class CopilotInterface:
    def __init__(self, config, history=None, pt_input=None, pt_output=None, rich_file=None):
        self.config = config
        self.history = history or InMemoryHistory()
        self.pt_input = pt_input
        self.pt_output = pt_output
        self.ai_service = AIService(config)
        
        if rich_file:
            self.console = Console(theme=connpy_theme, force_terminal=True, file=rich_file)
        else:
            self.console = Console(theme=connpy_theme)
            
        self.mode_range, self.mode_single, self.mode_lines = 0, 1, 2

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
            blocks = self.ai_service.build_context_blocks(raw_bytes, cmd_byte_positions, node_info)
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
                action = await confirm_session.prompt_async(HTML(f"<ansi{style}>Send? (y/n/e/range) [n]: </ansi{style}>"), key_bindings=c_bindings)
            except (KeyboardInterrupt, EOFError):
                action = "n"

            def parse_indices(text, max_len):
                """Helper to parse '1-3, 5, 7' into [0, 1, 2, 4, 6]."""
                indices = []
                # Replace commas with spaces and split
                parts = text.replace(',', ' ').split()
                for part in parts:
                    if '-' in part:
                        try:
                            start, end = map(int, part.split('-'))
                            # Ensure inclusive and 0-indexed
                            indices.extend(range(start-1, end))
                        except: continue
                    elif part.isdigit():
                        indices.append(int(part)-1)
                # Filter valid indices and remove duplicates
                return [i for i in sorted(set(indices)) if 0 <= i < max_len]

            action_l = (action or "n").lower().strip()
            if action_l in ('y', 'yes', 'all'):
                return "send_all", commands, None
            
            # Check for numeric selection (e.g., "1, 2-4")
            if re.match(r'^[0-9,\-\s]+$', action_l):
                selected_idxs = parse_indices(action_l, len(commands))
                if selected_idxs:
                    return "send_all", [commands[i] for i in selected_idxs], None

            elif action_l.startswith('e'):
                # Check if it's a selective edit like 'e1-2'
                selection_str = action_l[1:].strip()
                if selection_str:
                    idxs = parse_indices(selection_str, len(commands))
                    cmds_to_edit = [commands[i] for i in idxs] if idxs else commands
                else:
                    cmds_to_edit = commands

                target = "\n".join(cmds_to_edit)
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

