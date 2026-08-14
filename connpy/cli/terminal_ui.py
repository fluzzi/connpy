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
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import has_completions

from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory

from ..printer import connpy_theme
from connpy.utils import log_cleaner

class CopilotInterface:
    def __init__(self, config, history=None, pt_input=None, pt_output=None, rich_file=None, session_state=None):
        from ..services.ai_service import AIService
        self.config = config
        self.history = history or InMemoryHistory()
        self.pt_input = pt_input
        self.pt_output = pt_output
        self.rich_file = rich_file
        self.ai_service = AIService(config)
        self.mode_range, self.mode_single, self.mode_lines = 0, 1, 2 

        self.session_state = session_state if session_state is not None else {}
        self.session_state.setdefault('persona', 'engineer')
        self.session_state.setdefault('trust_mode', False)
        self.session_state.setdefault('memories', [])
        self.session_state.setdefault('copilot_chat_history', [])
        self.session_state.setdefault('os', None)
        self.session_state.setdefault('prompt', None)
        self.session_state.setdefault('context_mode', self.mode_range)
        self.session_state.setdefault('context_cmd', 1)
        self.session_state.setdefault('context_lines', 50)
        self.session_state.setdefault('last_total_cmds', None)
        self.session_state.setdefault('last_total_lines', None)

        if rich_file:
            self.console = Console(theme=connpy_theme, force_terminal=True, file=rich_file)
        else:
            self.console = Console(theme=connpy_theme)

    def _sync_session_context(self, state: dict):
        """Persist current context mode, depth, total commands, and total lines into session_state."""
        self.session_state['context_mode'] = state['context_mode']
        self.session_state['context_cmd'] = state['context_cmd']
        self.session_state['context_lines'] = state['context_lines']
        self.session_state['last_total_cmds'] = state['total_cmds']
        self.session_state['last_total_lines'] = state['total_lines']

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
            
            total_cmds = len(blocks)
            total_lines = len(buffer.split('\n'))

            saved_mode = self.session_state.get('context_mode', self.mode_range)
            saved_cmd = self.session_state.get('context_cmd', 1)
            saved_lines = self.session_state.get('context_lines', min(50, total_lines))
            last_total_cmds = self.session_state.get('last_total_cmds', None)
            last_total_lines = self.session_state.get('last_total_lines', None)

            is_range = saved_mode in (self.mode_range, 0, 'RANGE', 'range')
            is_lines = saved_mode in (self.mode_lines, 2, 'LINES', 'lines')
            is_single = saved_mode in (self.mode_single, 1, 'SINGLE', 'single')

            if is_range or is_single:
                is_mission = self.session_state.get('mission', {}).get('active', False)
                if last_total_cmds is not None and total_cmds > last_total_cmds and (saved_cmd > 1 or is_mission):
                    new_cmds = total_cmds - last_total_cmds
                    initial_cmd = saved_cmd + new_cmds
                else:
                    initial_cmd = saved_cmd
                initial_lines = saved_lines
            elif is_lines:
                if last_total_lines is not None and total_lines > last_total_lines and saved_lines > 50:
                    new_lines = total_lines - last_total_lines
                    initial_lines = saved_lines + new_lines
                else:
                    initial_lines = saved_lines
                initial_cmd = saved_cmd
            else:
                initial_cmd = saved_cmd
                initial_lines = saved_lines

            state = {
                'context_cmd': min(max(1, initial_cmd), max(1, total_cmds)),
                'total_cmds': total_cmds,
                'total_lines': total_lines,
                'context_lines': min(max(1, initial_lines), max(1, total_lines)),
                'context_mode': saved_mode,
                'cancelled': False,
                'toolbar_msg': '',
                'msg_expiry': 0
            }
            self.session_state['context_mode'] = saved_mode
            self.session_state['context_cmd'] = max(1, initial_cmd)
            self.session_state['context_lines'] = max(1, initial_lines)
            self.session_state['last_total_cmds'] = total_cmds
            self.session_state['last_total_lines'] = total_lines
            
            # 1. Visual Separation (Only show help banner on initial entry)
            self.console.print("") # Real line break
            self.console.print(Rule(title="[bold cyan] AI TERMINAL COPILOT [/bold cyan]", style="cyan"))
            if not self.session_state.get('banner_shown', False):
                self.console.print(Panel(
                    "[dim]Type your question. Enter to send, Escape/Ctrl+C to cancel. Type / for commands.\n"
                    "Tab to change context mode. Ctrl+\u2191/\u2193 to adjust context. \u2191\u2193 for question history.[/dim]",
                    border_style="cyan"
                ))
                self.session_state['banner_shown'] = True
            self.console.print("") # Small space before the copilot prompt

            bindings = KeyBindings()
            @bindings.add('c-up')
            def _(event):
                if state['context_mode'] == self.mode_lines:
                    state['context_lines'] = min(state['context_lines'] + 50, state['total_lines'])
                else:
                    state['context_cmd'] = min(state['context_cmd'] + 1, state['total_cmds'])
                self._sync_session_context(state)
                event.app.invalidate()
            @bindings.add('c-down')
            def _(event):
                if state['context_mode'] == self.mode_lines:
                    state['context_lines'] = max(state['context_lines'] - 50, min(50, state['total_lines']))
                else:
                    state['context_cmd'] = max(state['context_cmd'] - 1, 1)
                self._sync_session_context(state)
                event.app.invalidate()
            @bindings.add('tab')
            def _(event):
                buf = event.current_buffer
                # If typing a slash command (no spaces yet), use tab to autocomplete inline
                if buf.text.startswith('/') and ' ' not in buf.text:
                    buf.complete_next()
                else:
                    state['context_mode'] = (state['context_mode'] + 1) % 3
                    self._sync_session_context(state)
                    event.app.invalidate()
            @bindings.add('escape', eager=True)
            @bindings.add('c-c')
            def _(event):
                state['cancelled'] = True
                event.app.exit(result='')

            # Multiline keybindings: Enter to submit, Ctrl+Enter (c-j) or Alt+Enter to add a newline
            @bindings.add('enter', filter=~has_completions)
            def _(event):
                event.current_buffer.validate_and_handle()

            @bindings.add('c-j')
            @bindings.add('escape', 'enter')
            def _(event):
                event.current_buffer.insert_text('\n')

            def get_active_buffer():
                if state['context_mode'] == self.mode_lines:
                    return '\n'.join(buffer.split('\n')[-state['context_lines']:])
                idx = max(0, state['total_cmds'] - state['context_cmd'])
                start, end, preview = blocks[idx]
                if state['context_mode'] == self.mode_single:
                    active_raw = raw_bytes[start:end]
                else:
                    # Concat only the bytes of valid blocks to skip intermediate empty/cancelled prompt noise
                    active_raw = b"".join(raw_bytes[b[0]:b[1]] for b in blocks[idx:])
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
                    # Only show command help if typing the first command and there are no spaces
                    if text.startswith('/') and ' ' not in text:
                        commands = ['/os', '/prompt', '/architect', '/engineer', '/trust', '/untrust', '/memorize', '/clear', '/mission', '/cancel']
                        matches = [c for c in commands if c.startswith(text.lower())]
                        if matches:
                            m_text = html.escape(f"Available: {' '.join(matches)}")
                            return HTML(f'<style fg="{c_warning}">{m_text}</style>' + " " * 20)

                m_label = {self.mode_range: "RANGE", self.mode_single: "SINGLE", self.mode_lines: "LINES"}[state['context_mode']]
                if state['context_mode'] == self.mode_lines:
                    base_str = f'\u25b6 [Tab: {m_label}] {state["context_lines"]}/{state["total_lines"]}L (Ctrl+\u2191/\u2193 adjusts lines)'
                else:
                    idx = max(0, state['total_cmds'] - state['context_cmd'])
                    
                    def clean_preview(text):
                        # Clean newlines and the initial prompt (all up to #, > or $) to leave only the command
                        original = text.strip().replace('\r', '').replace('\n', ' ')
                        cleaned = re.sub(r'^.*?[#>\$]\s*', '', original)
                        # If cleaning the prompt leaves us with an empty string (e.g. it was just "iol#"), return the original
                        return cleaned if cleaned else original

                    if state['context_mode'] == self.mode_range:
                        range_blocks = blocks[idx:]
                        # If there is more than one block, the last one is always the empty/current prompt. We omit it visually.
                        if len(range_blocks) > 1:
                            range_blocks = range_blocks[:-1]
                            
                        # Clean and truncate very long commands so they don't break the UI
                        previews = []
                        for b in range_blocks:
                            p = clean_preview(b[2])
                            if p:
                                # Truncar comandos individuales largos
                                if len(p) > 25: p = p[:22].rstrip(' .,-_') + "..."
                                previews.append(p)
                        
                        if not previews:
                            desc = clean_preview(blocks[idx][2])
                        elif len(previews) <= 3:
                            desc = " + ".join(previews)
                        else:
                            desc = f"{previews[0]} + {previews[1]} + {previews[2]} (+{len(previews)-3})"
                    else:
                        # Modo SINGLE original
                        desc = clean_preview(blocks[idx][2])
                        
                    base_str = f'\u25b6 [Tab: {m_label}] {desc}'
                
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
                                ('/clear', 'Clear memory'),
                                ('/mission', 'Start autonomous mission'),
                                ('/cancel', 'Cancel active mission')
                            ]
                            for cmd, desc in commands:
                                if cmd.startswith(cmd_part.lower()):
                                    yield Completion(cmd, start_position=-len(cmd_part), display_meta=desc)

            copilot_completer = SlashCommandCompleter()

            def _finalize_mission(reason="completed", final_guide=""):
                mission = self.session_state.get('mission', {})
                if not mission or not mission.get('active', False):
                    return
                mission['active'] = False
                if reason == "completed":
                    self.console.print("\n[bold green]🎉 Mission Completed[/bold green]")
                elif reason == "user_cancelled":
                    self.console.print("\n[yellow]Mission cancelled by user.[/yellow]")
                elif reason == "limit_reached":
                    self.console.print("\n[yellow]Mission step limit reached.[/yellow]")

                final_notes = "\n".join(mission.get('scratchpad_notes', []))
                guide = final_guide or mission.get('last_guide', '')
                asst_msg = f"Notes: {final_notes}\nGuide: {guide}" if final_notes else guide
                hist = self.session_state.setdefault("copilot_chat_history", [])
                hist.append({"role": "user", "content": f"/mission {mission.get('goal', '')}"})
                hist.append({"role": "assistant", "content": asst_msg})
                self.session_state["copilot_chat_history"] = hist[-10:]

            while True:
                overrides = {}
                # Check for active mission auto-looping
                mission = self.session_state.get('mission', {})
                is_mission = mission.get('active', False) and not mission.get('paused', False)

                if is_mission:
                    # Force mode_range for mission mode
                    state['context_mode'] = self.mode_range
                    self.session_state['context_mode'] = self.mode_range

                    if mission.get('start_block_idx') is None:
                        mission['start_block_idx'] = state['total_cmds']

                    start_idx = mission.get('start_block_idx', state['total_cmds'])
                    cmds_since_start = max(1, (state['total_cmds'] - start_idx) + 1)
                    state['context_cmd'] = max(state.get('context_cmd', 1), cmds_since_start)
                    self.session_state['context_cmd'] = state['context_cmd']

                    step = mission.get('step', 1)
                    max_steps = mission.get('max_steps', 10)

                    if step > max_steps:
                        ext_session = PromptSession(input=self.pt_input, output=self.pt_output)
                        c_warn = self._get_theme_color("warning", "yellow")
                        import html
                        p_warn = html.escape(f"[Mission Limit Reached ({max_steps} steps)] Extend mission for 10 more steps? (y/n) [y]: ")
                        try:
                            ext_ans = await ext_session.prompt_async(HTML(f'<style fg="{c_warn}" bold="true">{p_warn}</style>'))
                        except (KeyboardInterrupt, EOFError):
                            ext_ans = 'n'

                        if (ext_ans or 'y').lower().strip() in ('y', 'yes'):
                            mission['max_steps'] += 10
                        else:
                            goal = mission.get('goal', '')
                            question = f"[MISSION SUMMARY]: Step limit reached ({max_steps} steps). Provide a concise summary of all findings and current status for: {goal}"
                            clean_question = question
                            mission['active'] = False
                            is_mission = False
                            self.console.print(f"\n[bold cyan]🤖 Generating Final Mission Summary ({max_steps} steps reached)...[/bold cyan]")

                if is_mission:
                    goal = mission.get('goal', '')
                    step = mission.get('step', 1)
                    question = f"[MISSION STEP {step}]: Continue analysis of: {goal}"
                    
                    scratchpad = mission.get('scratchpad_notes', [])
                    if scratchpad:
                        notes_text = "\n".join(f"- {n}" for n in scratchpad)
                        question += f"\n\nPast Mission Notes:\n{notes_text}"
                        
                    clean_question = question
                    self.console.print(f"\n[bold cyan]🤖 Executing Mission Step {step}: {goal}[/bold cyan]")
                elif not is_mission and 'clean_question' in locals() and clean_question.startswith("[MISSION SUMMARY]"):
                    # Pass-through to AI execution for generating final summary
                    pass
                else:
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
                        question = await session.prompt_async(
                            get_prompt_text, 
                            key_bindings=bindings, 
                            bottom_toolbar=get_toolbar,
                            multiline=True
                        )
                    except (KeyboardInterrupt, EOFError):
                        state['cancelled'] = True
                        question = ""
                    
                    if state['cancelled'] or not question.strip() or question.strip().lower() in ['cancel', 'exit', 'quit']:
                        _finalize_mission("user_cancelled")
                        return "cancel", None, None

                    # 3. Process Input via AIService
                    directive = self.ai_service.process_copilot_input(question, self.session_state)
                    
                    if directive["action"] == "mission_start":
                        mission = self.session_state.get('mission', {})
                        mission['start_block_idx'] = state['total_cmds']
                        state['context_mode'] = self.mode_range
                        self.session_state['context_mode'] = self.mode_range
                        state['context_cmd'] = 1
                        self.session_state['context_cmd'] = 1
                        clean_question = f"[MISSION STEP 1]: {directive['clean_prompt']}"
                        overrides = directive.get("overrides", {})
                        self.console.print(f"\n[bold cyan]🤖 Starting Mission: {directive['clean_prompt']}[/bold cyan]")
                    elif directive["action"] == "mission_cancel":
                        _finalize_mission("user_cancelled")
                        state['toolbar_msg'] = 'Mission cancelled'
                        continue
                    elif directive["action"] == "state_update":
                        msg = directive['message']
                        state['toolbar_msg'] = msg
                        state['msg_expiry'] = time.time() + 3 # 3 seconds timeout
                        
                        async def delayed_refresh():
                            await asyncio.sleep(3.1)
                            if state.get('toolbar_msg') == msg:
                                state['toolbar_msg'] = ''
                                try:
                                    from prompt_toolkit.application.current import get_app
                                    app = get_app()
                                    if app: app.invalidate()
                                except: pass
                        asyncio.create_task(delayed_refresh())

                        sys.stdout.write('\x1b[1A\x1b[2K')
                        sys.stdout.flush()
                        continue
                    else:
                        state['toolbar_msg'] = ''
                        if mission.get('active', False):
                            mission['paused'] = False
                            step = mission.get('step', 1)
                            goal = mission.get('goal', '')
                            clean_question = f"[MISSION FEEDBACK (Step {step})]: User provided guidance: {question}. Continue the mission for goal: {goal}"
                            self.console.print(f"\n[bold cyan]🤖 Continuing Mission with User Feedback: {question}[/bold cyan]")
                            overrides = directive.get("overrides", {})
                        else:
                            clean_question = directive.get("clean_prompt", question)
                            overrides = directive.get("overrides", {})
                
                # Merge node_info with session_state and overrides
                merged_node_info = node_info.copy()
                if self.session_state['os']: merged_node_info['os'] = self.session_state['os']
                if self.session_state['prompt']: merged_node_info['prompt'] = self.session_state['prompt']
                merged_node_info['persona'] = self.session_state['persona']
                merged_node_info['trust'] = self.session_state['trust_mode']
                merged_node_info['memories'] = list(self.session_state['memories'])
                merged_node_info['chat_history'] = list(self.session_state.get('copilot_chat_history', []))
                
                for k, v in overrides.items():
                    merged_node_info[k] = v

                # 3. AI Execution
                active_persona = merged_node_info.get('persona', self.session_state.get('persona', 'engineer'))
                persona_color = self._get_theme_color(active_persona, fallback="cyan")
                persona_title = "Network Architect" if active_persona == "architect" else "Network Engineer"
                
                active_buffer = get_active_buffer()
                
                live_text = ""
                first_chunk = True
                
                from rich.rule import Rule
                from rich.status import Status
                from connpy.printer import IncrementalMarkdownParser
                
                md_parser = IncrementalMarkdownParser(console=self.console)
                
                status_spinner = Status(
                    f"[bold {persona_color}]{persona_title}:[/bold {persona_color}] [dim]Thinking...[/dim]",
                    console=self.console,
                    spinner="dots"
                )
                status_spinner.start()
                
                def on_chunk(text):
                    nonlocal live_text, first_chunk
                    if first_chunk:
                        status_spinner.stop()
                        self.console.print(Rule(
                            f"[bold {persona_color}]{persona_title}[/bold {persona_color}]",
                            style=persona_color
                        ))
                        first_chunk = False
                    live_text += text
                    md_parser.feed(text)
                
                ai_task = asyncio.create_task(on_ai_call(active_buffer, clean_question, on_chunk, merged_node_info))
                
                try:
                    while not ai_task.done():
                        await asyncio.sleep(0.05)
                    result = await ai_task
                except asyncio.CancelledError:
                    status_spinner.stop()
                    _finalize_mission("user_cancelled")
                    return "cancel", None, None
                
                if first_chunk:
                    status_spinner.stop()

                if not first_chunk:
                    md_parser.flush()
                    self.console.print(Rule(style=persona_color))
                
                if not result or result.get("error"):
                    if first_chunk and result and result.get("error"):
                        self.console.print(f"[red]Error: {result['error']}[/red]")
                    _finalize_mission("user_cancelled")
                    return "cancel", None, None

                if first_chunk and result and result.get("guide"):
                    from rich.markdown import Markdown
                    self.console.print(Panel(Markdown(result["guide"]), title=f"[bold {persona_color}]{persona_title}[/bold {persona_color}]", border_style=persona_color))

                # Update copilot_chat_history or mission scratchpad
                if result and not result.get("error"):
                    guide = result.get("guide", "")
                    notes = result.get("notes", "")
                    mission = self.session_state.get('mission', {})
                    if mission.get('active', False):
                        if notes:
                            mission.setdefault('scratchpad_notes', []).append(notes)
                        if guide:
                            mission['last_guide'] = guide
                    
                    if guide or notes:
                        asst_msg = f"Notes: {notes}\nGuide: {guide}" if notes else guide
                        if not asst_msg and guide: asst_msg = guide
                        hist = self.session_state.setdefault("copilot_chat_history", [])
                        hist.append({"role": "user", "content": clean_question})
                        hist.append({"role": "assistant", "content": asst_msg})
                        self.session_state["copilot_chat_history"] = hist[-10:]
                commands = result.get("commands", [])
                mission = self.session_state.get('mission', {})
                if not commands:
                    if mission.get('active', False):
                        _finalize_mission("completed", result.get("guide", ""))
                    self.console.print("")
                    return "continue", None, None

                risk = result.get("risk_level", "low")
                risk_style = {"low": "success", "high": "warning", "destructive": "error"}.get(risk, "success")
                style_color = self._get_theme_color(risk_style, fallback="green")
                
                cmd_text = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(commands))
                self.console.print(Panel(cmd_text, title=f"[bold {style_color}]Suggested Commands [{risk.upper()}][/bold {style_color}]", border_style=f"bold {style_color}"))

                if merged_node_info.get('trust', False) and risk != "destructive":
                    self.console.print(f"[dim]⚙️ Auto-executing (Trust Mode)[/dim]")
                    if mission.get('active', False):
                        mission['step'] = mission.get('step', 1) + 1
                    return "send_all", commands, None

                confirm_session = PromptSession(input=self.pt_input, output=self.pt_output)
                c_bindings = KeyBindings()
                @c_bindings.add('escape', eager=True)
                @c_bindings.add('c-c')
                def _(ev): ev.app.exit(result='n')
                
                import html
                try:
                    p_text = html.escape(f"Send? (y/n/e/range) [n]: ")
                    action = await confirm_session.prompt_async(HTML(f'<style fg="{style_color}" bold="true">{p_text}</style>'), key_bindings=c_bindings)
                except (KeyboardInterrupt, EOFError):
                    _finalize_mission("user_cancelled", result.get("guide", ""))
                    self.console.print("")
                    return "continue", None, None

                def parse_indices(text, max_len):
                    indices = []
                    parts = text.replace(',', ' ').split()
                    for part in parts:
                        if '-' in part:
                            try:
                                start, end = map(int, part.split('-'))
                                indices.extend(range(start-1, end))
                            except: continue
                        elif part.isdigit():
                            indices.append(int(part)-1)
                    return [i for i in sorted(set(indices)) if 0 <= i < max_len]

                action_l = (action or "n").lower().strip()
                if action_l in ('y', 'yes', 'all'):
                    if mission.get('active', False):
                        mission['step'] = mission.get('step', 1) + 1
                    return "send_all", commands, None
                
                if re.match(r'^[0-9,\-\s]+$', action_l):
                    selected_idxs = parse_indices(action_l, len(commands))
                    if selected_idxs:
                        if mission.get('active', False):
                            mission['step'] = mission.get('step', 1) + 1
                        return "send_all", [commands[i] for i in selected_idxs], None

                elif action_l.startswith('e'):
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
                        if mission.get('active', False):
                            mission['paused'] = True
                            self.console.print("\n[yellow]⏸️  Mission Paused — Provide feedback to redirect, or type /cancel to abort.[/yellow]")
                        self.console.print("")
                        return "continue", None, None

                    if edited and edited.strip():
                        lines = [l.strip() for l in edited.split('\n') if l.strip()]
                        if mission.get('active', False):
                            mission['step'] = mission.get('step', 1) + 1
                        return "custom", None, lines

                # User rejected/cancelled the commands
                if mission.get('active', False):
                    mission['paused'] = True
                    self.console.print("\n[yellow]⏸️  Mission Paused — Provide feedback to redirect, or type /cancel to abort.[/yellow]")
                    
                self.console.print("")
                return "continue", None, None
            
            return "cancel", None, None

        finally:
            state['cancelled'] = True

