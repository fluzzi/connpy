import os
import re
import sys
import time
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
    def __init__(self, config, history=None, pt_input=None, pt_output=None, rich_file=None, session_state=None):
        self.config = config
        self.history = history or InMemoryHistory()
        self.pt_input = pt_input
        self.pt_output = pt_output
        self.ai_service = AIService(config)
        self.session_state = session_state if session_state is not None else {
            'persona': 'engineer',
            'trust_mode': False,
            'memories': [],
            'os': None,
            'prompt': None
        }

        if rich_file:
            self.console = Console(theme=connpy_theme, force_terminal=True, file=rich_file)
        else:
            self.console = Console(theme=connpy_theme)

        self.mode_range, self.mode_single, self.mode_lines = 0, 1, 2 

    def _get_theme_color(self, style_name: str, fallback: str = "white") -> str:
        """Extract Hex or ANSI color name from the active rich theme."""
        try:
            style = connpy_theme.styles.get(style_name)
            if style and style.color:
                # If it's a standard color like 'green', Rich might return its hex triplet
                if style.color.is_default: return fallback
                return style.color.triplet.hex if style.color.triplet else style.color.name
        except: pass
        return fallback

    async def run_session(self, 
                          raw_bytes: bytes, 
                          node_info: dict,
                          on_ai_call: Callable,
                          cmd_byte_positions: List[tuple] = None, 
                          blocks: List[tuple] = None):
        """
        Runs the interactive Copilot session.
        on_ai_call: async function(active_buffer, question) -> result_dict
        """
        from rich.rule import Rule
        
        try:
            # Prepare UI state
            buffer = log_cleaner(raw_bytes.decode(errors='replace'))
            
            # Use pre-calculated blocks if provided (remote mode), otherwise calculate locally (local mode)
            if blocks is None:
                last_line = buffer.split('\n')[-1].strip() if buffer.strip() else "(prompt)"
                blocks = self.ai_service.build_context_blocks(raw_bytes, cmd_byte_positions, node_info, last_line=last_line)
            
            state = {
                'context_cmd': 1,
                'total_cmds': len(blocks),
                'total_lines': len(buffer.split('\n')),
                'context_lines': min(50, len(buffer.split('\n'))),
                'context_mode': self.mode_range,
                'cancelled': False,
                'toolbar_msg': '',
                'msg_expiry': 0
            }
            
            # 1. Visual Separation
            self.console.print("") # Salto de línea real
            self.console.print(Rule(title="[bold cyan] AI TERMINAL COPILOT [/bold cyan]", style="cyan"))
            self.console.print(Panel(
                "[dim]Type your question. Enter to send, Escape/Ctrl+C to cancel. Type / for commands.\n"
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
                buf = event.current_buffer
                # If typing a slash command (no spaces yet), use tab to autocomplete inline
                if buf.text.startswith('/') and ' ' not in buf.text:
                    buf.complete_next()
                else:
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
                start, end, preview = blocks[idx]
                if state['context_mode'] == self.mode_single:
                    active_raw = raw_bytes[start:end]
                else:
                    active_raw = raw_bytes[start:]
                return preview + "\n" + log_cleaner(active_raw.decode(errors='replace'))

            def get_prompt_text():
                import html
                # Always use user_prompt color for the Ask prompt
                color = self._get_theme_color("user_prompt", "cyan")
                
                if state['context_mode'] == self.mode_lines:
                    text = html.escape(f"Ask [Ctx: {state['context_lines']}/{state['total_lines']}L]: ")
                    return HTML(f'<style fg="{color}">{text}</style>')
                active = get_active_buffer()
                lines_count = len(active.split('\n'))
                mode_str = {self.mode_range: "Range", self.mode_single: "Cmd"}[state['context_mode']]
                text = html.escape(f"Ask [{mode_str} {state['context_cmd']} ~{lines_count}L]: ")
                return HTML(f'<style fg="{color}">{text}</style>')

            from prompt_toolkit.application.current import get_app

            def get_toolbar():
                import html
                app = get_app()
                c_warning = self._get_theme_color("warning", "yellow")
                
                if app and app.current_buffer:
                    text = app.current_buffer.text
                    # Solo mostrar ayuda de comandos si estamos escribiendo el primer comando y no hay espacios
                    if text.startswith('/') and ' ' not in text:
                        commands = ['/os', '/prompt', '/architect', '/engineer', '/trust', '/untrust', '/memorize', '/clear']
                        matches = [c for c in commands if c.startswith(text.lower())]
                        if matches:
                            m_text = html.escape(f"Available: {' '.join(matches)}")
                            return HTML(f'<style fg="{c_warning}">{m_text}</style>' + " " * 20)

                m_label = {self.mode_range: "RANGE", self.mode_single: "SINGLE", self.mode_lines: "LINES"}[state['context_mode']]
                if state['context_mode'] == self.mode_lines:
                    base_str = f'\u25b6 Ctrl+\u2191/\u2193 adjusts by 50 lines  [Tab: {m_label}]'
                else:
                    idx = max(0, state['total_cmds'] - state['context_cmd'])
                    desc = blocks[idx][2]
                    base_str = f'\u25b6 {desc}  [Tab: {m_label}]'
                
                # Wrap base_str in a style to maintain consistency and avoid glitches
                # The fg color will be inherited from bottom-toolbar global style if not specified here
                base_html = f'<span>{html.escape(base_str)}</span>'
                
                res_html = base_html
                if state.get('toolbar_msg'):
                    if time.time() < state.get('msg_expiry', 0):
                        msg = html.escape(state['toolbar_msg'])
                        res_html = f'<style fg="{c_warning}">⚙️ {msg}</style> | ' + base_html
                    else:
                        state['toolbar_msg'] = ''
                
                # Pad with spaces to ensure the line is cleared when the message disappears
                return HTML(res_html + " " * 20)

            from prompt_toolkit.completion import Completer, Completion
            class SlashCommandCompleter(Completer):
                def get_completions(self, document, complete_event):
                    text = document.text_before_cursor
                    if text.startswith('/'):
                        parts = text.split()
                        # Only autocomplete the first word
                        if len(parts) <= 1 or (len(parts) == 1 and not text.endswith(' ')):
                            cmd_part = parts[0] if parts else text
                            commands = [
                                ('/os', 'Set device OS (e.g. cisco_ios)'),
                                ('/prompt', 'Override prompt regex'),
                                ('/architect', 'Switch to Architect persona'),
                                ('/engineer', 'Switch to Engineer persona'),
                                ('/trust', 'Enable auto-execute'),
                                ('/untrust', 'Disable auto-execute'),
                                ('/memorize', 'Add fact to memory'),
                                ('/clear', 'Clear memory')
                            ]
                            for cmd, desc in commands:
                                if cmd.startswith(cmd_part.lower()):
                                    yield Completion(cmd, start_position=-len(cmd_part), display_meta=desc)

            copilot_completer = SlashCommandCompleter()

            while True:
                # 2. Ask question
                from prompt_toolkit.styles import Style
                c_contrast = self._get_theme_color("contrast", "gray")
                ui_style = Style.from_dict({
                    'bottom-toolbar': f'fg:{c_contrast}',
                })
                
                session = PromptSession(
                    history=self.history, 
                    input=self.pt_input, 
                    output=self.pt_output,
                    completer=copilot_completer,
                    reserve_space_for_menu=0,
                    style=ui_style
                )
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
                
                if state['cancelled'] or not question.strip() or question.strip().lower() in ['cancel', 'exit', 'quit']:
                    return "cancel", None, None

                # 3. Process Input via AIService
                directive = self.ai_service.process_copilot_input(question, self.session_state)
                
                if directive["action"] == "state_update":
                    state['toolbar_msg'] = directive['message']
                    state['msg_expiry'] = time.time() + 3 # 3 seconds timeout
                    
                    async def delayed_refresh():
                        await asyncio.sleep(3.1)
                        # Only invalidate if the message hasn't been replaced by a newer one
                        if state.get('toolbar_msg') == directive['message']:
                            state['toolbar_msg'] = '' # Explicitly clear
                            try:
                                from prompt_toolkit.application.current import get_app
                                app = get_app()
                                if app: app.invalidate()
                            except: pass
                    asyncio.create_task(delayed_refresh())

                    # Mover el cursor arriba y limpiar la línea para que el nuevo prompt reemplace al anterior
                    sys.stdout.write('\x1b[1A\x1b[2K')
                    sys.stdout.flush()
                    continue
                else:
                    # Limpiar el mensaje de la barra cuando se hace una pregunta real
                    state['toolbar_msg'] = ''
                
                clean_question = directive.get("clean_prompt", question)
                overrides = directive.get("overrides", {})
                
                # Merge node_info with session_state and overrides
                merged_node_info = node_info.copy()
                if self.session_state['os']: merged_node_info['os'] = self.session_state['os']
                if self.session_state['prompt']: merged_node_info['prompt'] = self.session_state['prompt']
                merged_node_info['persona'] = self.session_state['persona']
                merged_node_info['trust'] = self.session_state['trust_mode']
                merged_node_info['memories'] = list(self.session_state['memories'])
                
                for k, v in overrides.items():
                    merged_node_info[k] = v

                # Enrich question
                past = self.history.get_strings()
                if len(past) > 1:
                    clean_past = [q for q in past[-6:-1] if not q.startswith('/')]
                    if clean_past:
                        history_text = "\n".join(f"- {q}" for q in clean_past)
                        clean_question = f"Previous questions:\n{history_text}\n\nCurrent Question:\n{clean_question}"

                # 3. AI Execution
                # Use persona from overrides (one-shot) or from session state
                active_persona = merged_node_info.get('persona', self.session_state.get('persona', 'engineer'))
                persona_color = self._get_theme_color(active_persona, fallback="cyan")
                persona_title = "Network Architect" if active_persona == "architect" else "Network Engineer"
                
                active_buffer = get_active_buffer()
                live_text = "Thinking..."
                panel = Panel(live_text, title=f"[bold {persona_color}]{persona_title}[/bold {persona_color}]", border_style=persona_color)
                
                def on_chunk(text):
                    nonlocal live_text
                    if live_text == "Thinking...": live_text = ""
                    live_text += text
                
                with Live(panel, console=self.console, refresh_per_second=10) as live:
                    def update_live(t):
                        live.update(Panel(Markdown(t), title=f"[bold {persona_color}]{persona_title}[/bold {persona_color}]", border_style=persona_color))

                    wrapped_chunk = lambda t: (on_chunk(t), update_live(live_text))
                    
                    # Check for interruption during AI call
                    ai_task = asyncio.create_task(on_ai_call(active_buffer, clean_question, wrapped_chunk, merged_node_info))
                    
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
                    self.console.print(Panel(Markdown(result["guide"]), title=f"[bold {persona_color}]{persona_title}[/bold {persona_color}]", border_style=persona_color))

                commands = result.get("commands", [])
                if not commands:
                    self.console.print("")
                    return "continue", None, None

                risk = result.get("risk_level", "low")
                risk_style = {"low": "success", "high": "warning", "destructive": "error"}.get(risk, "success")
                style_color = self._get_theme_color(risk_style, fallback="green")
                
                cmd_text = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(commands))
                # Explicitly use 'bold style_color' for both TITLE and BORDER to ensure maximum consistency
                self.console.print(Panel(cmd_text, title=f"[bold {style_color}]Suggested Commands [{risk.upper()}][/bold {style_color}]", border_style=f"bold {style_color}"))

                if merged_node_info.get('trust', False) and risk != "destructive":
                    self.console.print(f"[dim]⚙️ Auto-executing (Trust Mode)[/dim]")
                    return "send_all", commands, None

                confirm_session = PromptSession(input=self.pt_input, output=self.pt_output)
                c_bindings = KeyBindings()
                @c_bindings.add('escape', eager=True)
                @c_bindings.add('c-c')
                def _(ev): ev.app.exit(result='n')
                
                import html
                try:
                    p_text = html.escape(f"Send? (y/n/e/range) [n]: ")
                    # Use the EXACT same style_color and force bold="true" for Prompt-Toolkit
                    action = await confirm_session.prompt_async(HTML(f'<style fg="{style_color}" bold="true">{p_text}</style>'), key_bindings=c_bindings)
                except (KeyboardInterrupt, EOFError):
                    self.console.print("")
                    return "continue", None, None

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
                    
                    c_edit = self._get_theme_color("user_prompt", "cyan")
                    import html
                    e_text = html.escape("Edit (Ctrl+Enter or Esc+Enter to submit):\n")
                    try:
                        edited = await confirm_session.prompt_async(
                            HTML(f'<style fg="{c_edit}">{e_text}</style>'),
                            default=target, multiline=True, key_bindings=e_bindings
                        )
                    except (KeyboardInterrupt, EOFError):
                        self.console.print("")
                        return "continue", None, None

                    if edited and edited.strip():
                        # Split by lines to ensure core.py applies delay between each command
                        lines = [l.strip() for l in edited.split('\n') if l.strip()]
                        return "custom", None, lines
                    
                self.console.print("")
                return "continue", None, None
            
            return "cancel", None, None

        finally:
            state['cancelled'] = True
            self.console.print("[dim]Returning to session...[/dim]")

