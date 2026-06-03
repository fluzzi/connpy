import sys
from rich.panel import Panel
from rich.markdown import Markdown
from rich.rule import Rule
from rich.prompt import Prompt

from .. import printer

console = printer.console
mdprint = console.print

class AIHandler:
    def __init__(self, app):
        self.app = app

    def dispatch(self, args):
        if args.list_sessions:
            limit = 20 if not getattr(args, "all", False) else None
            sessions, total = self.app.services.ai.list_sessions(limit=limit)
            if not sessions:
                printer.info("No saved AI sessions found.")
                return
            
            columns = ["ID", "Title", "Created At", "Model"]
            rows = [[s["id"], s["title"], s["created_at"], s["model"]] for s in sessions]
            
            title = "AI Persisted Sessions"
            if limit and total > limit:
                title += f" (Showing last {limit} of {total})"
                
            printer.table(title, columns, rows)
            if limit and total > limit:
                printer.info(f"Use '--list --all' to see all {total} sessions.")
            return
            
        if args.delete_session:
            try:
                self.app.services.ai.delete_session(args.delete_session[0])
                printer.success(f"Session {args.delete_session[0]} deleted.")
            except Exception as e:
                printer.error(str(e))
            return

        if args.mcp is not None:
            return self.configure_mcp(args)
            
        # Determine session_id to resume
        session_id = None
        if args.resume:
            sessions, _ = self.app.services.ai.list_sessions()
            session_id = sessions[0]["id"] if sessions else None
            if not session_id:
                printer.warning("No previous session found to resume.")
        elif args.session:
            session_id = args.session[0]

        # Configure additional arguments for the AI service
        # Priority: CLI Args > Local Config
        settings = self.app.services.config_svc.get_settings().get("ai", {})
        arguments = {}
        
        for key in ["engineer_model", "engineer_api_key", "architect_model", "architect_api_key"]:
            cli_val = getattr(args, key, None)
            if cli_val:
                arguments[key] = cli_val[0]
            elif settings.get(key):
                arguments[key] = settings.get(key)

        for key in ["engineer_auth", "architect_auth"]:
            cli_val = getattr(args, key, None)
            if cli_val:
                arguments[key] = self._parse_auth_value(cli_val[0])
            elif settings.get(key):
                arguments[key] = settings.get(key)
        
        # Check keys only if running in local mode (not remote)
        if getattr(self.app.services, "mode", "local") == "local":
            if not arguments.get("engineer_api_key") and not arguments.get("engineer_auth"):
                printer.error("Engineer API key/auth not configured. The chat cannot start.")
                printer.info("Use 'connpy config --engineer-api-key <key>' or 'connpy config --engineer-auth <auth>' to set it.")
                sys.exit(1)
            if not arguments.get("architect_api_key") and not arguments.get("architect_auth"):
                printer.warning("Architect API key/auth not configured. Architect will be unavailable.")
                printer.info("Use 'connpy config --architect-api-key <key>' or 'connpy config --architect-auth <auth>' to enable it.")

        # The rest of the interaction is handled by the CLI with the underlying agent
        self.app.myai = self.app.services.ai
        self.ai_overrides = arguments
        
        if args.ask:
            self.single_question(args, session_id)
        else:
            self.interactive_chat(args, session_id)
            
    def single_question(self, args, session_id):
        query = " ".join(args.ask)
        with console.status("[ai_status]Agent is thinking and analyzing...[/ai_status]") as status:
            result = self.app.myai.ask(query, status=status, debug=args.debug, session_id=session_id, trust=args.trust, **self.ai_overrides)
        
        responder = result.get("responder", "engineer")
        border = "architect" if responder == "architect" else "engineer"
        title = "[architect][bold]Network Architect[/bold][/architect]" if responder == "architect" else "[engineer][bold]Network Engineer[/bold][/engineer]"
        
        if not result.get("streamed"):
            mdprint(Panel(Markdown(result["response"]), title=title, border_style=border, expand=False))
        
        if "usage" in result:
            u = result["usage"]
            console.print(f"[debug]Tokens: {u['total']} (Input: {u['input']}, Output: {u['output']})[/debug]")

    def interactive_chat(self, args, session_id):
        history = None
        if session_id:
            session_data = self.app.myai.load_session_data(session_id)
            if session_data:
                history = session_data.get("history", [])
                mdprint(Rule(title=f"[header] Resuming Session: {session_data.get('title')} [/header]", style="border"))
                if history:
                    mdprint(f"[debug]Analyzing {len(history)} previous messages...[/debug]\n")
            else:
                printer.info(f"Session '{session_id}' not found. Starting clean.")
        
        if not history:
            mdprint(Rule(style="engineer"))
            mdprint(Markdown("**Networking Expert Agent**: Hi! I'm your assistant. I can help you diagnose issues, run commands, and manage your nodes.\nType 'exit' to quit.\n"))
            mdprint(Rule(style="engineer"))
        
        while True:
            try:
                user_query = Prompt.ask("[user_prompt]User[/user_prompt]")
                if not user_query.strip(): continue
                if user_query.lower() in ['exit', 'quit', 'bye', 'cancel']: break
                
                with console.status("[ai_status]Agent is thinking...[/ai_status]") as status:
                    result = self.app.myai.ask(user_query, chat_history=history, status=status, debug=args.debug, trust=args.trust, session_id=session_id, **self.ai_overrides)
                
                new_history = result.get("chat_history")
                if new_history is not None:
                    history = new_history
                    
                responder = result.get("responder", "engineer")
                border = "architect" if responder == "architect" else "engineer"
                title = "[architect][bold]Network Architect[/bold][/architect]" if responder == "architect" else "[engineer][bold]Network Engineer[/bold][/engineer]"
                
                if not result.get("streamed"):
                    response_text = result.get("response", "")
                    if response_text:
                        mdprint(Panel(Markdown(response_text), title=title, border_style=border, expand=False))
                
                if "usage" in result:
                    u = result["usage"]
                    console.print(f"[debug]Tokens: {u['total']} (Input: {u['input']}, Output: {u['output']})[/debug]")
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Session closed.[/dim]")
                break

    def configure_mcp(self, args):
        """Handle MCP server configuration via CLI tokens or interactive wizard."""
        mcp_args = args.mcp
        
        # 1. Non-interactive CLI Mode (if arguments are provided)
        if mcp_args:
            action = mcp_args[0].lower()
            
            if action == "list":
                mcp_servers = self.app.services.ai.list_mcp_servers()
                if not mcp_servers:
                    printer.info("No MCP servers configured.")
                else:
                    columns = ["Name", "URL", "Enabled", "Auto-load OS"]
                    rows = []
                    for name, cfg in mcp_servers.items():
                        rows.append([
                            name, 
                            cfg.get("url", ""), 
                            "[green]Yes[/green]" if cfg.get("enabled", True) else "[red]No[/red]",
                            cfg.get("auto_load_on_os", "Any")
                        ])
                    printer.table("Configured MCP Servers", columns, rows)
                return

            elif action == "add":
                if len(mcp_args) < 3:
                    printer.error("Usage: connpy ai --mcp add <name> <url> [os_filter]")
                    return
                name, url = mcp_args[1], mcp_args[2]
                os_filter = mcp_args[3] if len(mcp_args) > 3 else None
                try:
                    self.app.services.ai.configure_mcp(name, url=url, auto_load_on_os=os_filter)
                    printer.success(f"MCP server '{name}' added/updated.")
                except Exception as e:
                    printer.error(str(e))
                return

            elif action == "remove":
                if len(mcp_args) < 2:
                    printer.error("Usage: connpy ai --mcp remove <name>")
                    return
                name = mcp_args[1]
                try:
                    self.app.services.ai.configure_mcp(name, remove=True)
                    printer.success(f"MCP server '{name}' removed.")
                except Exception as e:
                    printer.error(str(e))
                return

            elif action in ["enable", "disable"]:
                if len(mcp_args) < 2:
                    printer.error(f"Usage: connpy ai --mcp {action} <name>")
                    return
                name = mcp_args[1]
                enabled = (action == "enable")
                try:
                    self.app.services.ai.configure_mcp(name, enabled=enabled)
                    printer.success(f"MCP server '{name}' {'enabled' if enabled else 'disabled'}.")
                except Exception as e:
                    printer.error(str(e))
                return
            
            else:
                printer.error(f"Unknown MCP action: {action}")
                printer.info("Available actions: list, add, remove, enable, disable")
                return

        # 2. Interactive Wizard Mode (if no arguments provided)
        # Import forms dynamically to avoid circular dependencies if any
        if not hasattr(self.app, "cli_forms"):
            from .forms import Forms
            self.app.cli_forms = Forms(self.app)
            
        mcp_servers = self.app.services.ai.list_mcp_servers()
        
        result = self.app.cli_forms.mcp_wizard(mcp_servers)
        if not result:
            return

        action = result["action"]
        try:
            if action == "list":
                # Recursive call to the non-interactive list logic
                args.mcp = ["list"]
                return self.configure_mcp(args)
            
            elif action == "add":
                self.app.services.ai.configure_mcp(
                    result["name"], 
                    url=result["url"], 
                    enabled=result["enabled"],
                    auto_load_on_os=result["os"]
                )
                printer.success(f"MCP server '{result['name']}' saved.")
            
            elif action == "update": # Used for toggle
                self.app.services.ai.configure_mcp(
                    result["name"], 
                    enabled=result["enabled"]
                )
                printer.success(f"MCP server '{result['name']}' updated.")
                
            elif action == "remove":
                self.app.services.ai.configure_mcp(result["name"], remove=True)
                printer.success(f"MCP server '{result['name']}' removed.")
                
        except Exception as e:
            printer.error(str(e))

    def _parse_auth_value(self, value):
        if not value or value.lower() in ["none", "clear"]:
            return None
        import os
        import yaml
        import json
        if os.path.exists(value):
            try:
                with open(value, "r") as f:
                    content = f.read()
                try:
                    return json.loads(content)
                except ValueError:
                    return yaml.safe_load(content)
            except Exception as e:
                printer.error(f"Failed to read/parse auth file '{value}': {e}")
                sys.exit(1)
        
        try:
            return json.loads(value)
        except ValueError:
            try:
                parsed = yaml.safe_load(value)
                if isinstance(parsed, dict):
                    return parsed
                raise ValueError()
            except Exception:
                printer.error("Auth parameter must be a valid JSON/YAML string, or a path to a JSON/YAML file.")
                sys.exit(1)
