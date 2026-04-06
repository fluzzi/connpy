import os
import json
import re
import datetime
from textwrap import dedent
import litellm
from litellm import completion, stream_chunk_builder
from .core import nodes

# Silenciar feedback de litellm
litellm.suppress_debug_info = True
litellm.set_verbose = False
from .hooks import ClassHook, MethodHook
from . import printer
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

console = printer.console


@ClassHook
class ai:
    """Hybrid Multi-Agent System: Selective Escalation with Role Persistence."""

    SAFE_COMMANDS = [r'^show\s+', r'^ls\s*', r'^cat\s+', r'^ip\s+route\s+show', r'^ip\s+addr\s+show', r'^ip\s+link\s+show', r'^pwd$', r'^hostname$', r'^uname', r'^df\s*', r'^free\s*', r'^ps\s*', r'^ping\s+', r'^traceroute\s+']

    def __init__(self, config, org=None, api_key=None, engineer_model=None, architect_model=None, engineer_api_key=None, architect_api_key=None):
        self.config = config
        self.trusted_session = False  # Trust mode for the entire session
        
        # 1. Cargar configuración genérica
        aiconfig = self.config.config.get("ai", {})
        
        # Modelos (Prioridad: Argumento -> Config -> Default)
        self.engineer_model = engineer_model or aiconfig.get("engineer_model") or "gemini/gemini-3.1-flash-lite-preview"
        self.architect_model = architect_model or aiconfig.get("architect_model") or "anthropic/claude-sonnet-4-6"
        
        # API Keys (Prioridad: Argumento -> Config)
        self.engineer_key = engineer_api_key or aiconfig.get("engineer_api_key")
        self.architect_key = architect_api_key or aiconfig.get("architect_api_key")
        
        # Validate configuration
        if not self.engineer_key:
            raise ValueError("Engineer API key not configured. Use 'conn config ai engineer_api_key <key>' to set it.")
        if not self.architect_key:
            console.print("[yellow]Warning: Architect API key not configured. Architect will be unavailable.[/yellow]")
            console.print("[yellow]Use 'conn config ai architect_api_key <key>' to enable it.[/yellow]")
        
        # Límites
        self.max_history = 30
        self.max_truncate = 50000
        self.soft_limit_iterations = 20  # Show warning and suggest Ctrl+C
        self.hard_limit_iterations = 50  # Force stop

        # External tool registry (populated by plugins via ClassHook.modify)
        self.external_engineer_tools = []     # Tool defs for Engineer LLM
        self.external_architect_tools = []    # Tool defs for Architect LLM
        self.external_tool_handlers = {}      # {"tool_name": handler_callable}
        self.tool_status_formatters = {}      # {"tool_name": formatter_callable}
        self.engineer_prompt_extensions = []  # Extra text for engineer prompt
        self.architect_prompt_extensions = [] # Extra text for architect prompt

        # Long-term memory
        self.memory_path = os.path.join(self.config.defaultdir, "ai_memory.md")
        self.long_term_memory = ""
        if os.path.exists(self.memory_path):
            try:
                with open(self.memory_path, "r") as f:
                    self.long_term_memory = f.read()
            except FileNotFoundError:
                self.long_term_memory = ""
            except PermissionError as e:
                console.print(f"[yellow]Warning: Cannot read AI memory file: {e}[/yellow]")
            except Exception as e:
                console.print(f"[yellow]Warning: Failed to load AI memory: {e}[/yellow]")

        # Session Management
        self.sessions_dir = os.path.join(self.config.defaultdir, "ai_sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)
        self.session_id = None
        self.session_path = None

        # Prompts base agnósticos
        self._engineer_base_prompt = dedent(f"""
            Role: TECHNICAL EXECUTION ENGINE.
            Expertise: Universal Networking (Cisco, Nokia, Juniper, 6wind, etc.).
            
            Rules:
            - BE FAST: Execute tools directly to provide swift technical answers.
            - AUTONOMY: Proactively use iterative tool calls (list_nodes, run_commands) to find the root cause.
            - BATCH OPERATIONS: When working on multiple devices, call tools in parallel (multiple tool_calls in same response).
            - COMPLETE MISSIONS: Execute ALL steps of a mission before reporting back. Don't stop halfway.
            - DIAGRAM: Use ASCII art or Unicode box-drawing characters directly in your responses to visualize topologies or paths when helpful.
            - EVIDENCE: Include 'Key Snippets' from tool outputs. Be token-efficient.
            - NO WANDERING: Do not speculate. If stuck, report attempts.
            - SAFETY: When you use 'run_commands' with configuration commands, the system automatically prompts the user for confirmation. Just execute - don't ask permission first.
            
            CRITICAL - CONSULT vs ESCALATE:
            - ALWAYS use 'consult_architect' for: Configuration planning, design decisions, complex troubleshooting.
              Examples: "consultalo con el arquitecto", "preguntale al arquitecto", "que opina el arquitecto"
              You stay in control and present the advice to the user.
            
            - ONLY use 'escalate_to_architect' when user EXPLICITLY asks to TALK to the Architect:
              Examples: "quiero hablar con el arquitecto", "pasame con el arquitecto", "que me atienda el arquitecto"
              After escalation, you hand over control completely.
            
            - DEFAULT: When in doubt, use 'consult_architect'. Escalation is rare.
            
            Network Context: {self.long_term_memory if self.long_term_memory else "Empty."}
        """).strip()

        self._architect_base_prompt = dedent(f"""
            Role: STRATEGIC REASONING ENGINE.
            Expertise: Network Architecture, Complex Troubleshooting, and Design Validation.
            
            Rules:
            - STRATEGY: Define technical missions for the Engineer. 
            - DIAGRAM: Use ASCII art or Unicode box-drawing characters in your responses to visualize topologies, traffic paths, or logic flows.
            - ENGINEER CAPABILITIES: Your Engineer can:
                * Filter nodes (list_nodes), Run CLI commands (run_commands), Get metadata (get_node_info).
            - ANALYSIS: Review technical findings to identify patterns or design failures.
            - MEMORY: Update long-term facts ONLY when the user explicitly requests it.
            
            CRITICAL - EFFICIENT DELEGATION:
            - Plan ALL tasks upfront before delegating.
            - Delegate ONCE with a complete, detailed mission including ALL steps.
            - Example: "List all routers matching 'border.*', then run 'show ip bgp summary' and 'show ip route' on each, then analyze the outputs."
            - DO NOT delegate multiple times for the same goal. Batch everything into ONE mission.
            - Wait for Engineer's complete report before responding to user.
            
            CRITICAL - RETURNING CONTROL:
            - When your strategic analysis is complete and no further architectural decisions are needed, use 'return_to_engineer' to hand control back.
            - The Engineer is better suited for ongoing technical execution and troubleshooting.
            - Only stay in control if the user explicitly needs strategic oversight for multiple interactions.
            
            Network Context: {self.long_term_memory if self.long_term_memory else "Empty."}
        """).strip()

    @property
    def engineer_system_prompt(self):
        """Build engineer system prompt with plugin extensions."""
        if self.engineer_prompt_extensions:
            extensions = "\n".join(self.engineer_prompt_extensions)
            return self._engineer_base_prompt + f"\n\nPlugin Capabilities:\n{extensions}"
        return self._engineer_base_prompt

    @property
    def architect_system_prompt(self):
        """Build architect system prompt with plugin extensions."""
        if self.architect_prompt_extensions:
            extensions = "\n".join(self.architect_prompt_extensions)
            return self._architect_base_prompt + f"\n\nPlugin Capabilities:\n{extensions}"
        return self._architect_base_prompt

    def register_ai_tool(self, tool_definition, handler, target="engineer", engineer_prompt=None, architect_prompt=None, status_formatter=None):
        """Register an external tool for the AI system.

        Args:
            tool_definition (dict): OpenAI-compatible tool definition.
            handler (callable): Function(ai_instance, **tool_args) -> str.
            target (str): 'engineer', 'architect', or 'both'.
            engineer_prompt (str): Extra text for engineer system prompt.
            architect_prompt (str): Extra text for architect system prompt.
            status_formatter (callable): Function(args_dict) -> status string.
        """
        name = tool_definition["function"]["name"]
        if target in ("engineer", "both"):
            self.external_engineer_tools.append(tool_definition)
        if target in ("architect", "both"):
            self.external_architect_tools.append(tool_definition)
        self.external_tool_handlers[name] = handler
        if engineer_prompt:
            self.engineer_prompt_extensions.append(engineer_prompt)
        if architect_prompt:
            self.architect_prompt_extensions.append(architect_prompt)
        if status_formatter:
            self.tool_status_formatters[name] = status_formatter

    def _stream_completion(self, model, messages, tools, api_key, status=None, label="", debug=False, **kwargs):
        """Stream a completion call, rendering styled Markdown in real-time.
        
        Returns (response, streamed) where:
        - response: reconstructed ModelResponse (same as non-streaming)
        - streamed: True if text was rendered to console during streaming
        """
        from rich.live import Live
        
        stream_resp = completion(model=model, messages=messages, tools=tools, api_key=api_key, stream=True, **kwargs)
        
        chunks = []
        full_content = ""
        is_streaming_text = False
        has_tool_calls = False
        live_display = None
        
        # Determine styling based on current brain
        role_label = "Network Architect" if "architect" in label.lower() else "Network Engineer"
        border = "medium_purple" if "architect" in label.lower() else "blue"
        title = f"[bold {border}]{role_label}[/bold {border}]"
        
        try:
            for chunk in stream_resp:
                chunks.append(chunk)
                delta = chunk.choices[0].delta
                
                # Detect tool calls
                if hasattr(delta, 'tool_calls') and delta.tool_calls:
                    has_tool_calls = True
                
                # Stream text content with styled rendering
                if hasattr(delta, 'content') and delta.content and not debug:
                    full_content += delta.content
                    
                    if not is_streaming_text:
                        # Stop spinner before starting live display
                        if status:
                            status.stop()
                        live_display = Live(
                            Panel(Markdown(full_content), title=title, border_style=border, expand=False),
                            console=console,
                            refresh_per_second=8,
                            transient=False
                        )
                        live_display.start()
                        is_streaming_text = True
                    else:
                        live_display.update(
                            Panel(Markdown(full_content), title=title, border_style=border, expand=False)
                        )
        except Exception as e:
            if not chunks:
                raise
        finally:
            if live_display:
                # Render final state with complete content
                try:
                    live_display.update(
                        Panel(Markdown(full_content), title=title, border_style=border, expand=False)
                    )
                except Exception:
                    pass
                live_display.stop()
        
        # Rebuild complete response from chunks
        try:
            response = stream_chunk_builder(chunks, messages=messages)
        except Exception:
            # Fallback: manual reconstruction if stream_chunk_builder fails
            full_content_rebuilt = ""
            tool_calls_map = {}
            for c in chunks:
                d = c.choices[0].delta
                if hasattr(d, 'content') and d.content:
                    full_content_rebuilt += d.content
                if hasattr(d, 'tool_calls') and d.tool_calls:
                    for tc in d.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_map:
                            tool_calls_map[idx] = {"id": tc.id or "", "type": "function", "function": {"name": getattr(tc.function, 'name', '') or '', "arguments": getattr(tc.function, 'arguments', '') or ''}}
                        else:
                            if tc.id: tool_calls_map[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name: tool_calls_map[idx]["function"]["name"] = tc.function.name
                                if tc.function.arguments: tool_calls_map[idx]["function"]["arguments"] += tc.function.arguments
            
            # Build a minimal response-like object
            class FakeFunc:
                def __init__(self, name, arguments): self.name = name; self.arguments = arguments
            class FakeTC:
                def __init__(self, d): self.id = d["id"]; self.function = FakeFunc(d["function"]["name"], d["function"]["arguments"])
                def model_dump(self, **kw): return {"id": self.id, "type": "function", "function": {"name": self.function.name, "arguments": self.function.arguments}}
            class FakeMsg:
                def __init__(self, content, tcs): self.content = content or None; self.tool_calls = tcs if tcs else None; self.role = "assistant"
                def model_dump(self, **kw):
                    d = {"role": "assistant", "content": self.content}
                    if self.tool_calls: d["tool_calls"] = [tc.model_dump() for tc in self.tool_calls]
                    return d
            class FakeChoice:
                def __init__(self, msg): self.message = msg
            class FakeResp:
                def __init__(self, choice): self.choices = [choice]; self.usage = None
            
            tcs = [FakeTC(tool_calls_map[i]) for i in sorted(tool_calls_map)] if tool_calls_map else None
            response = FakeResp(FakeChoice(FakeMsg(full_content_rebuilt or full_content, tcs)))
        
        # Only count as "streamed" if we rendered text AND it was the final response (no tool calls)
        streamed = is_streaming_text and not has_tool_calls
        return response, streamed

    def _sanitize_messages(self, messages):
        """Sanitize message list for strict providers like Gemini.
        
        Ensures that:
        1. Every assistant message with tool_calls is followed by ALL its tool responses
        2. No user/system messages appear between tool_calls and tool responses
        3. Orphaned tool_calls at the end are removed
        4. Orphaned tool responses without a preceding tool_call are removed
        5. Incompatible metadata like cache_control is stripped for non-Anthropic models
        """
        if not messages:
            return messages
        
        # Pre-process messages to pull text from list contents (Anthropic cache format) 
        # and remove explicit cache keys.
        pre_sanitized = []
        for msg in messages:
            m = msg.copy() if isinstance(msg, dict) else msg.model_dump(exclude_none=True)
            
            # Convert content list to plain string if it's a system message with caching metadata
            if m.get('role') == 'system' and isinstance(m.get('content'), list):
                # Extraer texto de [{"type": "text", "text": "...", "cache_control": ...}]
                m['content'] = m['content'][0]['text'] if m['content'] else ""

            # Remove any explicit cache_control key anywhere
            if 'cache_control' in m: del m['cache_control']
            if isinstance(m.get('content'), list):
                for item in m['content']:
                    if isinstance(item, dict) and 'cache_control' in item: del item['cache_control']
            
            pre_sanitized.append(m)

        sanitized = []
        i = 0
        while i < len(pre_sanitized):
            msg = pre_sanitized[i]
            role = msg.get('role', '')
            
            if role == 'assistant' and msg.get('tool_calls'):
                # Collect all expected tool_call_ids
                expected_ids = set()
                for tc in msg['tool_calls']:
                    tc_id = tc.get('id') if isinstance(tc, dict) else getattr(tc, 'id', None)
                    if tc_id:
                        expected_ids.add(tc_id)
                
                # Look ahead for matching tool responses
                tool_responses = []
                j = i + 1
                while j < len(pre_sanitized):
                    next_msg = pre_sanitized[j]
                    if next_msg.get('role') == 'tool':
                        tool_responses.append(next_msg)
                        j += 1
                    else:
                        break
                
                # Only include this assistant+tools block if we have responses
                if tool_responses:
                    sanitized.append(msg)
                    sanitized.extend(tool_responses)
                    i = j
                else:
                    # Orphaned tool_calls with no responses - skip the assistant message
                    i += 1
            elif role == 'tool':
                # Orphaned tool response (no preceding assistant with tool_calls) - skip
                i += 1
            else:
                sanitized.append(msg)
                i += 1
        
        return sanitized

    def _truncate(self, text, limit=None):
        """Truncate text to specified limit, keeping head (60%) and tail (40%)."""
        final_limit = limit or self.max_truncate
        if len(text) <= final_limit: return text
        head_limit = int(final_limit * 0.6)
        tail_limit = int(final_limit * 0.4)
        return (text[:head_limit] + f"\n\n[... OUTPUT TRUNCATED ...]\n\n" + text[-tail_limit:])

    def manage_memory_tool(self, content, action="append"):
        """Save or update long-term memory. Only use when user explicitly requests it."""
        if not content or not content.strip():
            return "Error: Cannot save empty content to memory."
        
        try:
            mode = "a" if action == "append" else "w"
            os.makedirs(os.path.dirname(self.memory_path), exist_ok=True)
            with open(self.memory_path, mode) as f:
                timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
                f.write(f"\n\n## {timestamp}\n{content.strip()}\n" if action == "append" else content)
            
            # Reload memory after update
            with open(self.memory_path, "r") as f:
                self.long_term_memory = f.read()
            
            return "Memory updated successfully."
        except PermissionError as e:
            return f"Error: Permission denied writing to memory file: {e}"
        except Exception as e:
            return f"Error updating memory: {str(e)}"


    def list_nodes_tool(self, filter_pattern=".*"):
        """List nodes matching the filter pattern. Returns metadata for <=5 nodes, names only for more."""
        try:
            matched_names = self.config._getallnodes(filter_pattern)
            if not matched_names: return "No nodes found."
            if len(matched_names) <= 5:
                matched_data = self.config.getitems(matched_names, extract=True)
                res = {}
                for name, data in matched_data.items():
                    os_tag = "unknown"
                    if isinstance(data, dict):
                        ts = data.get("tags")
                        if isinstance(ts, dict): os_tag = ts.get("os", "unknown")
                    res[name] = {"os": os_tag}
                return json.dumps(res)
            return json.dumps({"count": len(matched_names), "nodes": matched_names, "note": "Use 'get_node_info' for details."})
        except Exception as e: 
            return f"Error listing nodes: {str(e)}"

    def _is_safe_command(self, cmd):
        """Check if a command matches safe patterns."""
        return any(re.match(pattern, cmd.strip(), re.IGNORECASE) for pattern in self.SAFE_COMMANDS)
    
    def run_commands_tool(self, nodes_filter, commands, status=None):
        """Execute commands on nodes matching the filter. Native interactive confirmation for unsafe commands."""
        # Handle if commands is a JSON string
        if isinstance(commands, str):
            try:
                commands = json.loads(commands)
            except ValueError:
                commands = [c.strip() for c in commands.split('\n') if c.strip()]
        
        # Expand multi-line commands within a list (in case the AI packs them)
        if isinstance(commands, list):
            expanded_commands = []
            for cmd in commands:
                expanded_commands.extend([c.strip() for c in str(cmd).split('\n') if c.strip()])
            commands = expanded_commands
        else:
            commands = [str(commands)]
        
        # Check command safety natively
        if not self.trusted_session:
            unsafe_commands = [cmd for cmd in commands if not self._is_safe_command(cmd)]
            if unsafe_commands:
                # Stop the spinner so prompt doesn't get messed up
                if status: status.stop()
                
                # Show ALL commands with unsafe ones highlighted
                formatted_cmds = []
                for cmd in commands:
                    if cmd in unsafe_commands:
                        formatted_cmds.append(f"  • [yellow]{cmd}[/yellow]")
                    else:
                        formatted_cmds.append(f"  • {cmd}")
                
                panel_content = f"Target: {nodes_filter}\nCommands:\n" + "\n".join(formatted_cmds)
                console.print(Panel(panel_content, title="[bold yellow]⚠️ UNSAFE COMMANDS DETECTED[/bold yellow]", border_style="yellow"))
                
                try:
                    from rich.prompt import Prompt
                    user_resp = Prompt.ask("[bold yellow]Execute? (y: yes / n: no / a: allow all this session / <text>: feedback)[/bold yellow]", default="n")
                except KeyboardInterrupt:
                    if status: status.update("[bold blue]Engineer: Resuming...")
                    console.print("[bold red]✗ Aborted by user (Ctrl+C).[/bold red]")
                    return "Error: User cancelled execution (Ctrl+C)."
                
                # Resume the spinner
                if status: status.update("[bold blue]Engineer: Processing user response...")
                
                user_resp_lower = user_resp.strip().lower()
                if user_resp_lower in ['a', 'allow']:
                    self.trusted_session = True
                    console.print("[bold green]✓ Trust Mode Enabled. All future commands in this session will execute without confirmation.[/bold green]")
                elif user_resp_lower in ['y', 'yes']:
                    console.print("[bold green]✓ Executing...[/bold green]")
                elif user_resp_lower in ['n', 'no', '']:
                    console.print("[bold red]✗ Execution rejected by user.[/bold red]")
                    return "Error: User rejected execution."
                else:
                    console.print(f"[bold cyan]User feedback: [/bold cyan]{user_resp}")
                    return f"User requested changes: {user_resp}. Please adjust the commands based on this feedback and try again."
        
        try:
            matched_names = self.config._getallnodes(nodes_filter)
            if not matched_names: return "No nodes found matching filter."
            thisnodes_dict = self.config.getitems(matched_names, extract=True)
            result = nodes(thisnodes_dict, config=self.config).run(commands)
            return self._truncate(json.dumps(result))
        except Exception as e: 
            return f"Error executing commands: {str(e)}"

    def get_node_info_tool(self, node_name):
        """Get detailed metadata for a specific node. Passwords are masked."""
        try:
            d = self.config.getitem(node_name, extract=True)
            if 'password' in d: d['password'] = '***'
            return json.dumps(d)
        except Exception as e: 
            return f"Error getting node info: {str(e)}"

    def _engineer_loop(self, task, status=None, debug=False, chat_history=None):
        """Internal loop where the Engineer executes technical tasks for the Architect."""
        # Optimización de caché para el Ingeniero (Solo para Anthropic directo, Vertex tiene reglas distintas)
        if "claude" in self.engineer_model.lower() and "vertex" not in self.engineer_model.lower():
            messages = [{"role": "system", "content": [{"type": "text", "text": self.engineer_system_prompt, "cache_control": {"type": "ephemeral"}}]}]
        else:
            messages = [{"role": "system", "content": self.engineer_system_prompt}]
            
        if chat_history:
            # Clean chat history from caching metadata if engineer is not a compatible Claude model
            if "claude" not in self.engineer_model.lower() or "vertex" in self.engineer_model.lower():
                messages.extend(self._sanitize_messages(chat_history[-5:]))
            else:
                messages.extend(chat_history[-5:])
        
        messages.append({"role": "user", "content": f"MISSION: {task}"})
        
        tools = self._get_engineer_tools()
        usage = {"input": 0, "output": 0, "total": 0}
        iteration = 0
        soft_limit_warned = False
        
        try:
            while iteration < self.hard_limit_iterations:
                iteration += 1
                
                # Soft limit warning
                if iteration == self.soft_limit_iterations and not soft_limit_warned:
                    console.print(f"[yellow]⚠ Engineer has performed {iteration} steps. This is taking longer than expected.[/yellow]")
                    console.print(f"[yellow]  You can press Ctrl+C to interrupt and get a summary.[/yellow]")
                    soft_limit_warned = True
                
                if status: status.update(f"[bold blue]Engineer: Analyzing mission... (step {iteration})")
                
                try:
                    safe_messages = self._sanitize_messages(messages)
                    response = completion(model=self.engineer_model, messages=safe_messages, tools=tools, api_key=self.engineer_key)
                except Exception as e:
                    return f"Engineer failed to connect: {str(e)}", usage
                
                if hasattr(response, "usage") and response.usage:
                    usage["input"] += getattr(response.usage, "prompt_tokens", 0)
                    usage["output"] += getattr(response.usage, "completion_tokens", 0)
                    usage["total"] += getattr(response.usage, "total_tokens", 0)

                resp_msg = response.choices[0].message
                msg_dict = resp_msg.model_dump(exclude_none=True)
                if msg_dict.get("tool_calls") and msg_dict.get("content") == "": msg_dict["content"] = None
                messages.append(msg_dict)

                if not resp_msg.tool_calls: break
                for tc in resp_msg.tool_calls:
                    fn, args = tc.function.name, json.loads(tc.function.arguments)
                    
                    # Notificación en tiempo real de la tarea técnica
                    if status:
                        if fn == "list_nodes": status.update(f"[bold blue]Engineer: [SEARCH] {args.get('filter_pattern','.*')}")
                        elif fn == "run_commands": 
                            cmds = args.get('commands', [])
                            cmd_str = cmds[0] if cmds else ""
                            status.update(f"[bold blue]Engineer: [CMD] {cmd_str}")
                        elif fn == "get_node_info": status.update(f"[bold blue]Engineer: [INSPECT] {args.get('node_name','')}")
                        elif fn in self.tool_status_formatters: status.update(self.tool_status_formatters[fn](args))

                    if debug: console.print(Panel(Text(json.dumps(args, indent=2)), title=f"[bold blue]Engineer Tool: {fn}[/bold blue]", border_style="blue"))
                    
                    if fn == "list_nodes": obs = self.list_nodes_tool(**args)
                    elif fn == "run_commands": obs = self.run_commands_tool(**args, status=status)
                    elif fn == "get_node_info": obs = self.get_node_info_tool(**args)
                    elif fn in self.external_tool_handlers: obs = self.external_tool_handlers[fn](self, **args)
                    else: obs = f"Error: Unknown tool '{fn}'."
                    
                    if debug: console.print(Panel(Text(str(obs)), title=f"[bold green]Engineer Observation: {fn}[/bold green]", border_style="green"))
                    messages.append({"tool_call_id": tc.id, "role": "tool", "name": fn, "content": obs})
            
            if iteration >= self.hard_limit_iterations:
                console.print(f"[red]⛔ Engineer reached hard limit ({self.hard_limit_iterations} steps). Forcing stop.[/red]")
            
            if debug and resp_msg.content:
                console.print(Panel(Text(resp_msg.content), title="[bold blue]Engineer Final Report to Architect[/bold blue]", border_style="blue"))
            
            return resp_msg.content, usage
        except Exception as e:
            return f"Engineer failed: {str(e)}", usage

    def _get_engineer_tools(self):
        """Define tools available to the Engineer."""
        tools = [
            {"type": "function", "function": {"name": "list_nodes", "description": "Lists available nodes in the inventory.", "parameters": {"type": "object", "properties": {"filter_pattern": {"type": "string", "description": "Regex to filter nodes (e.g. '.*', 'border.*')."}}}}},
            {"type": "function", "function": {"name": "run_commands", "description": "Runs one or more commands on matched nodes. MANDATORY: You MUST call 'list_nodes' first to verify the target list.", "parameters": {"type": "object", "properties": {"nodes_filter": {"type": "string", "description": "Exact node name or verified filter pattern."}, "commands": {"type": "array", "items": {"type": "string"}, "description": "List of commands (e.g. ['show ip route', 'show int desc'])."}}, "required": ["nodes_filter", "commands"]}}},
            {"type": "function", "function": {"name": "get_node_info", "description": "Gets full metadata for a specific node.", "parameters": {"type": "object", "properties": {"node_name": {"type": "string"}}, "required": ["node_name"]}}},
            {"type": "function", "function": {"name": "consult_architect", "description": "Ask the Strategic Reasoning Engine for advice on complex design, architecture, or troubleshooting decisions. You remain in control and will present the response to the user. Use this for: configuration planning, design validation, complex troubleshooting.", "parameters": {"type": "object", "properties": {"question": {"type": "string", "description": "Strategic question or decision needed."}, "technical_summary": {"type": "string", "description": "Technical findings and context gathered so far."}}, "required": ["question", "technical_summary"]}}},
            {"type": "function", "function": {"name": "escalate_to_architect", "description": "Transfer full control to the Strategic Reasoning Engine. Use ONLY when the user explicitly requests the Architect or when the problem requires strategic oversight beyond consultation. After escalation, the Architect takes over the conversation.", "parameters": {"type": "object", "properties": {"reason": {"type": "string", "description": "Why you're escalating (e.g. 'User requested Architect', 'Complex multi-site design needed')."}, "context": {"type": "string", "description": "Full context and findings to hand over."}}, "required": ["reason", "context"]}}}
        ]
        tools.extend(self.external_engineer_tools)
        return tools

    def _get_architect_tools(self):
        """Define tools available to the Strategic Reasoning Engine."""
        tools = [
            {"type": "function", "function": {"name": "delegate_to_engineer", "description": "Delegates a technical mission to the Engineer.", "parameters": {"type": "object", "properties": {"task": {"type": "string", "description": "Detailed technical mission or goal."}}, "required": ["task"]}}},
            {"type": "function", "function": {"name": "return_to_engineer", "description": "Return control to the Engineer. Use this when your strategic analysis is complete and the Engineer should handle the rest of the conversation.", "parameters": {"type": "object", "properties": {"summary": {"type": "string", "description": "Brief summary of your analysis to hand over to the Engineer."}}, "required": ["summary"]}}},
            {"type": "function", "function": {"name": "manage_memory_tool", "description": "Saves information to long-term memory. MANDATORY: Only use this if the user explicitly asks to remember or save something.", "parameters": {"type": "object", "properties": {"content": {"type": "string"}, "action": {"type": "string", "enum": ["append", "replace"]}}, "required": ["content"]}}}
        ]
        tools.extend(self.external_architect_tools)
        return tools

    def _get_sessions(self):
        """Returns a list of session metadata sorted by date."""
        sessions = []
        if not os.path.exists(self.sessions_dir):
            return []
        for f in os.listdir(self.sessions_dir):
            if f.endswith(".json"):
                path = os.path.join(self.sessions_dir, f)
                try:
                    with open(path, "r") as fs:
                        data = json.load(fs)
                        sessions.append({
                            "id": f[:-5],
                            "title": data.get("title", "Untitled Session"),
                            "created_at": data.get("created_at", "Unknown"),
                            "model": data.get("model", "Unknown"),
                            "path": path
                        })
                except Exception:
                    continue
        return sorted(sessions, key=lambda x: x["created_at"], reverse=True)

    def list_sessions(self):
        """Prints a list of sessions using printer.table."""
        sessions = self._get_sessions()
        if not sessions:
            printer.info("No saved AI sessions found.")
            return
        
        columns = ["ID", "Title", "Created At", "Model"]
        rows = [[s["id"], s["title"], s["created_at"], s["model"]] for s in sessions]
        printer.table("AI Persisted Sessions", columns, rows)

    def load_session_data(self, session_id):
        """Loads a session's raw data by ID."""
        path = os.path.join(self.sessions_dir, f"{session_id}.json")
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                    self.session_id = session_id
                    self.session_path = path
                    return data
            except Exception as e:
                printer.error(f"Failed to load session {session_id}: {e}")
        return None

    def delete_session(self, session_id):
        """Deletes a session by ID."""
        path = os.path.join(self.sessions_dir, f"{session_id}.json")
        if os.path.exists(path):
            os.remove(path)
            printer.success(f"Session {session_id} deleted.")
        else:
            printer.error(f"Session {session_id} not found.")

    def get_last_session_id(self):
        """Returns the ID of the most recent session."""
        sessions = self._get_sessions()
        return sessions[0]["id"] if sessions else None

    def _generate_session_id(self, query):
        """Generates a unique session ID based on timestamp."""
        return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    def save_session(self, history, title=None, model=None):
        """Saves current history to the session file."""
        if not self.session_id:
            # Generate ID from first user query if available
            first_user_msg = next((m["content"] for m in history if m["role"] == "user"), "new-session")
            self.session_id = self._generate_session_id(first_user_msg)
            self.session_path = os.path.join(self.sessions_dir, f"{self.session_id}.json")

        # If it's a new file, we might want to set a better title
        if not os.path.exists(self.session_path) and not title:
            raw_title = next((m["content"] for m in history if m["role"] == "user"), "New Session")
            # Clean title: remove newlines, multiple spaces
            clean_title = " ".join(raw_title.split())
            if len(clean_title) > 40:
                title = clean_title[:37].strip() + "..."
            else:
                title = clean_title

        try:
            # Read existing metadata if it exists
            metadata = {}
            if os.path.exists(self.session_path):
                with open(self.session_path, "r") as f:
                    metadata = json.load(f)
            
            metadata.update({
                "id": self.session_id,
                "title": title or metadata.get("title", "New Session"),
                "created_at": metadata.get("created_at", datetime.datetime.now().isoformat()),
                "updated_at": datetime.datetime.now().isoformat(),
                "model": model or metadata.get("model", self.engineer_model),
                "history": history
            })

            with open(self.session_path, "w") as f:
                json.dump(metadata, f, indent=4)
        except Exception as e:
            printer.error(f"Failed to save session: {e}")

        except Exception as e:
            printer.error(f"Failed to save session: {e}")

    @MethodHook
    def ask(self, user_input, dryrun=False, chat_history=None, status=None, debug=False, stream=True, session_id=None):
        if chat_history is None: chat_history = []
        
        # Load session if provided and history is empty
        if session_id and not chat_history:
            session_data = self.load_session_data(session_id)
            if session_data:
                chat_history = session_data.get("history", [])
                # If we loaded history, the caller might need it back
                # But typically ask() is called in a loop with an external history object

        usage = {"input": 0, "output": 0, "total": 0}
        
        # 1. Selector de Rol inicial (Sticky Brain)
        explicit_architect = re.match(r'^(architect|arquitecto|@architect)[:\s]', user_input, re.I)
        explicit_engineer = re.match(r'^(engineer|ingeniero|@engineer)[:\s]', user_input, re.I)
        
        if explicit_architect:
            current_brain = "architect"
        elif explicit_engineer:
            current_brain = "engineer"
        else:
            # Sticky Brain: Detectar si el Arquitecto estaba al mando en el historial reciente
            is_architect_active = False
            for msg in reversed(chat_history[-5:]):
                tcs = msg.get('tool_calls') if isinstance(msg, dict) else getattr(msg, 'tool_calls', None)
                if tcs:
                    for tc in tcs:
                        fn = tc.get('function', {}).get('name') if isinstance(tc, dict) else getattr(getattr(tc, 'function', None), 'name', '')
                        # Architect stays in control if delegating tasks or if Engineer escalated to them
                        # consult_architect is just Engineer asking for advice - Engineer keeps control
                        if fn in ['delegate_to_engineer', 'escalate_to_architect']:
                            is_architect_active = True; break
                if is_architect_active: break
            current_brain = "architect" if is_architect_active else "engineer"
        
        # 2. Preparación de mensajes y limpieza
        clean_input = re.sub(r'^(architect|arquitecto|engineer|ingeniero|@architect|@engineer)[:\s]+', '', user_input, flags=re.IGNORECASE).strip()
        
        system_prompt = self.architect_system_prompt if current_brain == "architect" else self.engineer_system_prompt
        tools = self._get_architect_tools() if current_brain == "architect" else self._get_engineer_tools()
        model = self.architect_model if current_brain == "architect" else self.engineer_model
        key = self.architect_key if current_brain == "architect" else self.engineer_key

        # Estructura optimizada para Prompt Caching (Solo para Anthropic directo, Vertex tiene reglas distintas)
        if "claude" in model.lower() and "vertex" not in model.lower():
            messages = [{"role": "system", "content": [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]}]
        else:
            messages = [{"role": "system", "content": system_prompt}]
        
        # Interleaving de historial
        last_role = "system"
        # Sanitize history if the current target model is not compatible with cache_control
        history_to_process = chat_history[-self.max_history:]
        if "claude" not in model.lower() or "vertex" in model.lower():
            history_to_process = self._sanitize_messages(history_to_process)

        for msg in history_to_process:
            m = msg if isinstance(msg, dict) else msg.model_dump(exclude_none=True)
            role = m.get('role')
            if role == last_role and role == 'user':
                messages[-1]['content'] += "\n" + (m.get('content') or "")
                continue
            if role == 'assistant' and m.get('tool_calls') and m.get('content') == "": m['content'] = None
            messages.append(m)
            last_role = role

        if last_role == 'user': messages[-1]['content'] += "\n" + clean_input
        else: messages.append({"role": "user", "content": clean_input})

        # 3. Bucle de ejecución
        iteration = 0
        soft_limit_warned = False
        streamed_response = False
        
        try:
            while iteration < self.hard_limit_iterations:
                iteration += 1
                
                # Soft limit warning
                if iteration == self.soft_limit_iterations and not soft_limit_warned:
                    console.print(f"[yellow]⚠ Agent has performed {iteration} steps. This is taking longer than expected.[/yellow]")
                    console.print(f"[yellow]  You can press Ctrl+C to interrupt and get a summary of progress.[/yellow]")
                    soft_limit_warned = True
                
                label = "[bold medium_purple]Architect" if current_brain == "architect" else "[bold blue]Engineer"
                if status: status.update(f"{label} is thinking... (step {iteration})")
                
                streamed_response = False
                try:
                    safe_messages = self._sanitize_messages(messages)
                    if stream and not debug:
                        response, streamed_response = self._stream_completion(
                            model=model, messages=safe_messages, tools=tools, api_key=key,
                            status=status, label=label, debug=debug, num_retries=3
                        )
                    else:
                        response = completion(model=model, messages=safe_messages, tools=tools, api_key=key, num_retries=3)
                except Exception as e:
                    if current_brain == "architect":
                        if status: status.update("[bold orange3]Architect unavailable! Falling back to Engineer...")
                        # Preserve context when falling back - use clean_input directly
                        current_brain = "engineer"
                        model = self.engineer_model
                        tools = self._get_engineer_tools()
                        key = self.engineer_key
                        # Rebuild messages with Engineer system prompt and original user request
                        messages = [{"role": "system", "content": self.engineer_system_prompt}]
                        # Add chat history if exists (excluding system prompt)
                        if chat_history:
                            for msg in chat_history[-self.max_history:]:
                                if msg.get('role') != 'system':
                                    messages.append(msg)
                        # Add current user request
                        messages.append({"role": "user", "content": clean_input})
                        continue
                    else: 
                        return {"response": f"Error: Both engines failed. {str(e)}", "chat_history": messages[1:], "usage": usage}
                
                if hasattr(response, "usage") and response.usage:
                    usage["input"] += getattr(response.usage, "prompt_tokens", 0)
                    usage["output"] += getattr(response.usage, "completion_tokens", 0)
                    usage["total"] += getattr(response.usage, "total_tokens", 0)

                resp_msg = response.choices[0].message
                msg_dict = resp_msg.model_dump(exclude_none=True)
                if msg_dict.get("tool_calls") and msg_dict.get("content") == "": msg_dict["content"] = None
                messages.append(msg_dict)

                if debug and resp_msg.content:
                    console.print(Panel(Markdown(resp_msg.content), title=f"{label} Reasoning", border_style="medium_purple" if current_brain == "architect" else "blue"))

                if not resp_msg.tool_calls: break
                
                # Track if we need to inject a user message after all tool responses
                pending_user_message = None
                
                for tc in resp_msg.tool_calls:
                    fn, args = tc.function.name, json.loads(tc.function.arguments)
                    
                    # Validate tool access based on current brain
                    if fn in ['delegate_to_engineer'] and current_brain != "architect":
                        obs = f"Error: Tool '{fn}' is only available to the Architect (Architect). You are the Engineer (Engineer). Use 'run_commands' directly to execute configuration."
                        messages.append({"tool_call_id": tc.id, "role": "tool", "name": fn, "content": obs})
                        continue
                    
                    if status:
                        if fn == "delegate_to_engineer": status.update(f"[bold medium_purple]Architect: [DELEGATING MISSION] {args.get('task','')[:40]}...")
                        elif fn == "manage_memory_tool": status.update(f"[bold medium_purple]Architect: [UPDATING MEMORY]")

                    if debug: console.print(Panel(Text(json.dumps(args, indent=2)), title=f"{label} Decision: {fn}", border_style="white"))

                    if fn == "delegate_to_engineer":
                        obs, eng_usage = self._engineer_loop(args["task"], status=status, debug=debug, chat_history=messages[:-1])
                        usage["input"] += eng_usage["input"]; usage["output"] += eng_usage["output"]; usage["total"] += eng_usage["total"]
                    elif fn == "consult_architect":
                        if status: status.update("[bold medium_purple]Engineer consulting Architect...")
                        try:
                            # Consultation only - Engineer stays in control
                            claude_resp = completion(
                                model=self.architect_model, 
                                messages=[
                                    {"role": "system", "content": self.architect_system_prompt},
                                    {"role": "user", "content": f"The Engineer needs your strategic advice.\n\nTECHNICAL SUMMARY: {args['technical_summary']}\n\nQUESTION: {args['question']}\n\nProvide strategic guidance. The Engineer will continue handling the user."}
                                ], 
                                api_key=self.architect_key, 
                                num_retries=3
                            )
                            obs = claude_resp.choices[0].message.content
                            if debug: console.print(Panel(Markdown(obs), title="[bold medium_purple]Architect Consultation[/bold medium_purple]", border_style="medium_purple"))
                        except Exception as e:
                            if status: status.update("[bold orange3]Architect unavailable! Engineer continuing alone...")
                            obs = f"Architect unavailable ({str(e)}). Proceeding with your best technical judgment."
                    
                    elif fn == "escalate_to_architect":
                        if status: status.update("[bold medium_purple]Transferring control to Architect...")
                        # Full escalation - Architect takes over
                        current_brain = "architect"
                        model = self.architect_model
                        tools = self._get_architect_tools()
                        key = self.architect_key
                        messages[0] = {"role": "system", "content": self.architect_system_prompt}
                        # Prepare handover context to inject AFTER all tool responses
                        handover_msg = f"HANDOVER FROM EXECUTION ENGINE\n\nReason: {args['reason']}\n\nContext: {args['context']}\n\nYou are now in control of this conversation."
                        pending_user_message = handover_msg
                        obs = "Control transferred to Architect. Handover context will be provided."
                        if debug: console.print(Panel(Text(handover_msg), title="[bold medium_purple]Escalation to Architect[/bold medium_purple]", border_style="medium_purple"))
                    
                    elif fn == "return_to_engineer":
                        if status: status.update("[bold blue]Transferring control back to Engineer...")
                        # Architect returns control to Engineer
                        current_brain = "engineer"
                        model = self.engineer_model
                        tools = self._get_engineer_tools()
                        key = self.engineer_key
                        messages[0] = {"role": "system", "content": self.engineer_system_prompt}
                        # Prepare handover context to inject AFTER all tool responses
                        handover_msg = f"HANDOVER FROM ARCHITECT\n\nSummary: {args['summary']}\n\nYou are now back in control. Continue handling the user's requests."
                        pending_user_message = handover_msg
                        obs = "Control returned to Engineer. Handover summary will be provided."
                        if debug: console.print(Panel(Text(handover_msg), title="[bold blue]Return to Engineer[/bold blue]", border_style="blue"))
                    
                    elif fn == "list_nodes": obs = self.list_nodes_tool(**args)
                    elif fn == "run_commands": obs = self.run_commands_tool(**args, status=status)
                    elif fn == "get_node_info": obs = self.get_node_info_tool(**args)
                    elif fn == "manage_memory_tool": obs = self.manage_memory_tool(**args)
                    elif fn in self.external_tool_handlers: obs = self.external_tool_handlers[fn](self, **args)
                    else: obs = f"Error: {fn} unknown."
                    
                    messages.append({"tool_call_id": tc.id, "role": "tool", "name": fn, "content": obs})
                
                # Inject pending user message AFTER all tool responses are added
                if pending_user_message:
                    messages.append({"role": "user", "content": pending_user_message})
            
            if iteration >= self.hard_limit_iterations:
                console.print(f"[red]⛔ Agent reached hard limit ({self.hard_limit_iterations} steps). Forcing stop to prevent infinite loop.[/red]")
                # Only inject user message if we're not in the middle of tool calls
                last_msg = messages[-1] if messages else {}
                if last_msg.get("role") != "assistant" or not last_msg.get("tool_calls"):
                    messages.append({"role": "user", "content": "Hard iteration limit reached. Please provide a summary of your findings so far."})
                    try:
                        safe_messages = self._sanitize_messages(messages)
                        response = completion(model=model, messages=safe_messages, tools=[], api_key=key)
                        resp_msg = response.choices[0].message
                        messages.append(resp_msg.model_dump(exclude_none=True))
                    except Exception as e:
                        if status:
                            status.update(f"[bold red]Error fetching summary: {e}[/bold red]")
                        printer.warning(f"Failed to fetch final summary from LLM: {e}")
        except KeyboardInterrupt:
            if status: status.update("[bold red]Interrupted! Closing pending tasks...")
            last_msg = messages[-1]
            if last_msg.get("tool_calls"):
                for tc in last_msg["tool_calls"]:
                    messages.append({"tool_call_id": tc.get("id"), "role": "tool", "name": tc.get("function", {}).get("name"), "content": "Operation cancelled by user."})
            messages.append({"role": "user", "content": "USER INTERRUPTED. Briefly summarize what you were doing and stop."})
            try:
                safe_messages = self._sanitize_messages(messages)
                response = completion(model=model, messages=safe_messages, tools=tools, api_key=key)
                resp_msg = response.choices[0].message
                messages.append(resp_msg.model_dump(exclude_none=True))
            except Exception: pass
        finally:
            # Auto-save session
            self.save_session(messages, model=model)

        return {
            "response": messages[-1].get("content"), 
            "chat_history": messages[1:], 
            "app_related": True, 
            "usage": usage,
            "responder": current_brain,  # "architect" or "engineer"
            "streamed": streamed_response
        }

    @MethodHook
    def confirm(self, user_input): return True
