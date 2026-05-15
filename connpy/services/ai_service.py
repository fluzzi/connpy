import re
from .base import BaseService
from .exceptions import InvalidConfigurationError
from connpy.utils import log_cleaner

class AIService(BaseService):
    """Business logic for interacting with AI agents and LLM configurations."""

    def build_context_blocks(self, raw_bytes: bytes, cmd_byte_positions: list, node_info: dict, last_line: str = "") -> list:
        """Identifies command blocks in the terminal history."""
        blocks = []
        if not raw_bytes:
            return blocks
            
        default_prompt = r'>$|#$|\$$|>.$|#.$|\$.$'
        device_prompt = node_info.get("prompt", default_prompt) if isinstance(node_info, dict) else default_prompt
        prompt_re_str = re.sub(r'(?<!\\)\$', '', device_prompt)
        try:
            prompt_re = re.compile(prompt_re_str)
        except Exception:
            prompt_re = re.compile(re.sub(r'(?<!\\)\$', '', default_prompt))
            
        if cmd_byte_positions and len(cmd_byte_positions) >= 1:
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

        # Always ensure there is a final block representing the current prompt
        # Find the start of the last line in the raw buffer to avoid selecting everything 
        # when no commands have been executed yet.
        last_newline = raw_bytes.rfind(b'\n')
        current_prompt_pos = last_newline + 1 if last_newline != -1 else 0
        
        if not blocks:
            blocks.append((current_prompt_pos, last_line[:80] if last_line else "CURRENT CONTEXT"))
        elif blocks[-1][0] < current_prompt_pos:
            # If the last command block ends before the current prompt, add the prompt block
            blocks.append((current_prompt_pos, last_line[:80] if last_line else "CURRENT CONTEXT"))

        return blocks

    def process_copilot_input(self, input_text: str, session_state: dict) -> dict:
        """Parses slash commands and manages session state. Returns directive dict."""
        text = input_text.strip()
        if not text.startswith('/'):
            return {"action": "execute", "clean_prompt": text, "overrides": {}}
            
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        # 1. State Commands (Persistent)
        if cmd == "/os":
            if args:
                session_state['os'] = args
                return {"action": "state_update", "message": f"OS context changed to {args}"}
        elif cmd == "/prompt":
            if args:
                session_state['prompt'] = args
                return {"action": "state_update", "message": f"Prompt regex changed to {args}"}
        elif cmd == "/memorize":
            if args:
                session_state['memories'].append(args)
                return {"action": "state_update", "message": f"Memory added: {args}"}
        elif cmd == "/clear":
            session_state['memories'] = []
            return {"action": "state_update", "message": "Memory cleared"}
            
        # 2. Hybrid Commands
        elif cmd == "/architect":
            if not args:
                session_state['persona'] = 'architect'
                return {"action": "state_update", "message": "Persona set to Architect"}
            else:
                return {"action": "execute", "clean_prompt": args, "overrides": {"persona": "architect"}}
                
        elif cmd == "/engineer":
            if not args:
                session_state['persona'] = 'engineer'
                return {"action": "state_update", "message": "Persona set to Engineer"}
            else:
                return {"action": "execute", "clean_prompt": args, "overrides": {"persona": "engineer"}}
                
        elif cmd == "/trust":
            if not args:
                session_state['trust_mode'] = True
                return {"action": "state_update", "message": "Auto-execute (trust) enabled for session"}
            else:
                return {"action": "execute", "clean_prompt": args, "overrides": {"trust": True}}
                
        elif cmd == "/untrust":
            if not args:
                session_state['trust_mode'] = False
                return {"action": "state_update", "message": "Auto-execute (trust) disabled for session"}
            else:
                return {"action": "execute", "clean_prompt": args, "overrides": {"trust": False}}

        # Unknown command, execute normally
        return {"action": "execute", "clean_prompt": text, "overrides": {}}

    def ask(self, input_text, dryrun=False, chat_history=None, status=None, debug=False, session_id=None, console=None, chunk_callback=None, confirm_handler=None, trust=False, **overrides):
        """Send a prompt to the AI agent."""
        from connpy.ai import ai
        agent = ai(self.config, console=console, confirm_handler=confirm_handler, trust=trust, **overrides)
        return agent.ask(input_text, dryrun, chat_history, status=status, debug=debug, session_id=session_id, chunk_callback=chunk_callback)


    def confirm(self, input_text, console=None):
        """Ask for a safe confirmation of an action."""
        from connpy.ai import ai
        agent = ai(self.config, console=console)
        return agent.confirm(input_text)

    def ask_copilot(self, terminal_buffer, user_question, node_info=None, chunk_callback=None):
        """Ask the AI copilot for terminal assistance."""
        from connpy.ai import ai, run_ai_async
        agent = ai(self.config)
        future = run_ai_async(agent.aask_copilot(terminal_buffer, user_question, node_info, chunk_callback=chunk_callback))
        return future.result()

    async def aask_copilot(self, terminal_buffer, user_question, node_info=None, chunk_callback=None):
        """Ask the AI copilot for terminal assistance asynchronously."""
        from connpy.ai import ai, run_ai_async
        import asyncio
        agent = ai(self.config)
        future = run_ai_async(agent.aask_copilot(terminal_buffer, user_question, node_info, chunk_callback=chunk_callback))
        return await asyncio.wrap_future(future)


    def list_sessions(self):
        """Return a list of all saved AI sessions."""
        from connpy.ai import ai
        agent = ai(self.config)
        return agent._get_sessions()

    def delete_session(self, session_id):
        """Delete an AI session by ID."""
        import os
        sessions_dir = os.path.join(self.config.defaultdir, "ai_sessions")
        path = os.path.join(sessions_dir, f"{session_id}.json")
        if os.path.exists(path):
            os.remove(path)
        else:
            raise InvalidConfigurationError(f"Session '{session_id}' not found.")

    def configure_provider(self, provider, model=None, api_key=None):
        """Update AI provider settings in the configuration."""
        settings = self.config.config.get("ai", {})
        if model:
            settings[f"{provider}_model"] = model
        if api_key:
            settings[f"{provider}_api_key"] = api_key
            
        self.config.config["ai"] = settings
        self.config._saveconfig(self.config.file)

    def configure_mcp(self, name, url=None, enabled=None, auto_load_on_os=None, remove=False):
        """Update MCP server settings in the configuration with smart merging."""
        ai_settings = self.config.config.get("ai", {})
        mcp_servers = ai_settings.get("mcp_servers", {})
        
        if remove:
            if name in mcp_servers:
                del mcp_servers[name]
        else:
            # Get existing or new
            server_cfg = mcp_servers.get(name, {})
            
            # Partial updates
            if url is not None:
                server_cfg["url"] = url
            
            if enabled is not None:
                server_cfg["enabled"] = bool(enabled)
            elif "enabled" not in server_cfg:
                server_cfg["enabled"] = True # Default for new entries
                
            if auto_load_on_os is not None:
                if auto_load_on_os == "": # Explicit clear
                    if "auto_load_on_os" in server_cfg:
                        del server_cfg["auto_load_on_os"]
                else:
                    server_cfg["auto_load_on_os"] = auto_load_on_os
            
            mcp_servers[name] = server_cfg
            
        ai_settings["mcp_servers"] = mcp_servers
        self.config.config["ai"] = ai_settings
        self.config._saveconfig(self.config.file)

    def load_session_data(self, session_id):
        """Load a session's raw data by ID."""
        from connpy.ai import ai
        agent = ai(self.config)
        return agent.load_session_data(session_id)

