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
            sessions = self.app.services.ai.list_sessions()
            if not sessions:
                printer.info("No saved AI sessions found.")
                return
            columns = ["ID", "Title", "Created At", "Model"]
            rows = [[s["id"], s["title"], s["created_at"], s["model"]] for s in sessions]
            printer.table("AI Persisted Sessions", columns, rows)
            return
            
        if args.delete_session:
            try:
                self.app.services.ai.delete_session(args.delete_session[0])
                printer.success(f"Session {args.delete_session[0]} deleted.")
            except Exception as e:
                printer.error(str(e))
            return
            
        # Determinar session_id para retomar
        session_id = None
        if args.resume:
            sessions = self.app.services.ai.list_sessions()
            session_id = sessions[0]["id"] if sessions else None
            if not session_id:
                printer.warning("No previous session found to resume.")
        elif args.session:
            session_id = args.session[0]

        # Configurar argumentos adicionales para el servicio de AI
        # Prioridad: CLI Args > Configuración Local
        settings = self.app.services.config_svc.get_settings().get("ai", {})
        arguments = {}
        
        for key in ["engineer_model", "engineer_api_key", "architect_model", "architect_api_key"]:
            cli_val = getattr(args, key, None)
            if cli_val:
                arguments[key] = cli_val[0]
            elif settings.get(key):
                arguments[key] = settings.get(key)
        
        # Check keys only if running in local mode (not remote)
        if getattr(self.app.services, "mode", "local") == "local":
            if not arguments.get("engineer_api_key"):
                printer.error("Engineer API key not configured. The chat cannot start.")
                printer.info("Use 'connpy config --engineer-api-key <key>' to set it.")
                sys.exit(1)
            if not arguments.get("architect_api_key"):
                printer.warning("Architect API key not configured. Architect will be unavailable.")
                printer.info("Use 'connpy config --architect-api-key <key>' to enable it.")

        # El resto de la interacción el CLI la maneja con el agente subyacente
        self.app.myai = self.app.services.ai
        self.ai_overrides = arguments
        
        if args.ask:
            self.single_question(args, session_id)
        else:
            self.interactive_chat(args, session_id)
            
    def single_question(self, args, session_id):
        query = " ".join(args.ask)
        with console.status("[ai_status]Agent is thinking and analyzing...") as status:
            result = self.app.myai.ask(query, status=status, debug=args.debug, session_id=session_id, trust=args.trust, **self.ai_overrides)
        
        responder = result.get("responder", "engineer")
        border = "architect" if responder == "architect" else "engineer"
        title = "[architect][bold]Network Architect[/bold][/architect]" if responder == "architect" else "[engineer][bold]Network Engineer[/bold][/engineer]"
        
        if not result.get("streamed"):
            mdprint(Panel(Markdown(result["response"]), title=title, border_style=border, expand=False))
        
        if "usage" in result:
            u = result["usage"]
            console.print(f"[debug]Tokens: {u['total']} (Input: {u['input']}, Output: {u['output']})[/debug]")
        console.print()

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
                printer.error(f"Could not load session {session_id}. Starting clean.")
        
        if not history:
            mdprint(Rule(style="engineer"))
            mdprint(Markdown("**Networking Expert Agent**: Hi! I'm your assistant. I can help you diagnose issues, run commands, and manage your nodes.\nType 'exit' to quit.\n"))
            mdprint(Rule(style="engineer"))
        
        while True:
            try:
                user_query = Prompt.ask("[user_prompt]User[/user_prompt]")
                if not user_query.strip(): continue
                if user_query.lower() in ['exit', 'quit', 'bye']: break
                
                with console.status("[ai_status]Agent is thinking...") as status:
                    result = self.app.myai.ask(user_query, chat_history=history, status=status, debug=args.debug, trust=args.trust, **self.ai_overrides)
                
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
                console.print()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Session closed.[/dim]")
                break
