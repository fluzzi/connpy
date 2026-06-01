import os
import sys
import yaml
import threading
from rich.rule import Rule
from .. import printer
from ..services.exceptions import ConnpyError
from .help_text import get_instructions

class RunHandler:
    def __init__(self, app):
        self.app = app
        self.print_lock = threading.Lock()

    def dispatch(self, args):
        if len(args.data) > 1:
            args.action = "noderun"
        actions = {
            "noderun": self.node_run,
            "generate": self.yaml_generate,
            "generate_ai": self.ai_generate,
            "run": self.yaml_run
        }
        return actions.get(args.action)(args)

    def node_run(self, args):
        nodes_filter = args.data[0]
        
        # Resolve and filter nodes through context-aware list_nodes
        try:
            matched_nodes = self.app.services.nodes.list_nodes(nodes_filter)
        except Exception:
            matched_nodes = []
            
        if not matched_nodes:
            printer.error(f"No nodes found matching filter: {nodes_filter}")
            sys.exit(2)
            
        commands = [" ".join(args.data[1:])]

        # Check for Preflight AI simulation
        if getattr(args, "preflight_ai", False):
            matched_node_names = [n.get("name") if isinstance(n, dict) else n for n in matched_nodes]
            
            renderer = printer.BlockMarkdownRenderer()
            first_chunk = True
            status_context = printer.console.status("[ai_status]Simulating execution...[/ai_status]")
            
            def callback(chunk):
                nonlocal first_chunk
                if first_chunk:
                    try: status_context.stop()
                    except: pass
                    printer.console.print(Rule(title="[engineer][bold]Preflight AI Simulation[/bold][/engineer]", style="engineer"))
                    first_chunk = False
                renderer.feed(chunk)
            
            try:
                status_context.start()
                self.app.services.ai.predict_execution_results(
                    matched_node_names,
                    commands,
                    chunk_callback=callback
                )
                if first_chunk:
                    try: status_context.stop()
                    except: pass
                    printer.console.print(Rule(title="[engineer][bold]Preflight AI Simulation[/bold][/engineer]", style="engineer"))
                renderer.flush()
                printer.console.print(Rule(style="engineer"))
            except Exception as e:
                printer.error(f"Preflight AI simulation failed: {e}")
                sys.exit(1)
            sys.exit(0)

        try:
            header_printed = False

            if hasattr(args, 'test_expected') and args.test_expected:
                # Mode: Test
                def _on_node_complete(unique, node_output, node_status, node_result):
                    nonlocal header_printed
                    with self.print_lock:
                        if not header_printed:
                            printer.console.print(Rule("OUTPUT", style="header"))
                            header_printed = True
                    printer.test_panel(unique, node_output, node_status, node_result)

                results = self.app.services.execution.test_commands(
                    nodes_filter=matched_nodes,
                    commands=commands,
                    expected=args.test_expected,
                    on_node_complete=_on_node_complete
                )
                printer.test_summary(results)
            else:
                # Mode: Normal Run
                def _on_node_complete(unique, node_output, node_status):
                    nonlocal header_printed
                    with self.print_lock:
                        if not header_printed:
                            printer.console.print(Rule("OUTPUT", style="header"))
                            header_printed = True
                    printer.node_panel(unique, node_output, node_status)

                results = self.app.services.execution.run_commands(
                    nodes_filter=matched_nodes,
                    commands=commands,
                    on_node_complete=_on_node_complete
                )
                printer.run_summary(results)

            # Analyze execution results if requested
            if getattr(args, "analyze", None) is not None:
                printer.console.print()
                
                renderer = printer.BlockMarkdownRenderer()
                first_chunk = True
                status_context = printer.console.status("[ai_status]Analyzing execution results...[/ai_status]")
                
                def callback(chunk):
                    nonlocal first_chunk
                    if first_chunk:
                        try: status_context.stop()
                        except: pass
                        printer.console.print(Rule(title="[architect][bold]Network Architect AI Analysis[/bold][/architect]", style="architect"))
                        first_chunk = False
                    renderer.feed(chunk)
                
                query = args.analyze if args.analyze else " ".join(args.data[1:])
                try:
                    status_context.start()
                    self.app.services.ai.analyze_execution_results(
                        results,
                        query=query,
                        chunk_callback=callback
                    )
                    if first_chunk:
                        try: status_context.stop()
                        except: pass
                        printer.console.print(Rule(title="[architect][bold]Network Architect AI Analysis[/bold][/architect]", style="architect"))
                    renderer.flush()
                    printer.console.print(Rule(style="architect"))
                except Exception as e:
                    printer.error(f"AI Analysis failed: {e}")

        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)

    def yaml_generate(self, args):
        if os.path.exists(args.data[0]):
            printer.error(f"File '{args.data[0]}' already exists.")
            sys.exit(14)
        else:
            with open(args.data[0], "w") as file:
                file.write(get_instructions("generate"))
            printer.success(f"File {args.data[0]} generated successfully")
            sys.exit()

    def yaml_run(self, args):
        path = args.data[0]
        try:
            with open(path, "r") as f:
                playbook = yaml.load(f, Loader=yaml.FullLoader)

            # Check preflight first before any task runs
            if getattr(args, "preflight_ai", False):
                preflight_failed = False
                for task in playbook.get("tasks", []):
                    name = task.get("name", "Task")
                    nodelist = task.get("nodes", [])
                    commands = task.get("commands", [])
                    
                    # Resolve nodes to names
                    try:
                        if isinstance(nodelist, str):
                            resolved_nodes = self.app.services.nodes.list_nodes(nodelist)
                        elif isinstance(nodelist, list):
                            resolved_nodes = []
                            for item in nodelist:
                                matches = self.app.services.nodes.list_nodes(item)
                                for m in matches:
                                    if m not in resolved_nodes:
                                        resolved_nodes.append(m)
                        else:
                            resolved_nodes = []
                    except Exception:
                        resolved_nodes = []
                    
                    resolved_names = [n.get("name") if isinstance(n, dict) else n for n in resolved_nodes]
                    printer.console.print(f"\n[bold]Task: {name}[/bold] (Preflight for {len(resolved_names)} nodes)")
                    
                    renderer = printer.BlockMarkdownRenderer()
                    first_chunk = True
                    status_context = printer.console.status("[ai_status]Simulating execution...[/ai_status]")
                    
                    def callback(chunk):
                        nonlocal first_chunk
                        if first_chunk:
                            try: status_context.stop()
                            except: pass
                            printer.console.print(Rule(title=f"[engineer][bold]Preflight AI Simulation: {name}[/bold][/engineer]", style="engineer"))
                            first_chunk = False
                        renderer.feed(chunk)
                    try:
                        status_context.start()
                        self.app.services.ai.predict_execution_results(
                            resolved_names,
                            commands,
                            chunk_callback=callback
                        )
                        if first_chunk:
                            try: status_context.stop()
                            except: pass
                            printer.console.print(Rule(title=f"[engineer][bold]Preflight AI Simulation: {name}[/bold][/engineer]", style="engineer"))
                        renderer.flush()
                        printer.console.print(Rule(style="engineer"))
                    except Exception as e:
                        printer.error(f"Preflight AI simulation failed for task {name}: {e}")
                        preflight_failed = True
                if preflight_failed:
                    sys.exit(1)
                sys.exit(0)

            # Standard run
            results_all = {}
            for task in playbook.get("tasks", []):
                task_res = self.cli_run(task)
                if task_res:
                    results_all.update(task_res)

            # If analyze is enabled, run analysis on accumulated results
            if getattr(args, "analyze", None) is not None:
                printer.console.print()
                
                renderer = printer.BlockMarkdownRenderer()
                first_chunk = True
                status_context = printer.console.status("[ai_status]Analyzing playbook execution results...[/ai_status]")
                
                def callback(chunk):
                    nonlocal first_chunk
                    if first_chunk:
                        try: status_context.stop()
                        except: pass
                        printer.console.print(Rule(title="[architect][bold]Network Architect AI Playbook Analysis[/bold][/architect]", style="architect"))
                        first_chunk = False
                    renderer.feed(chunk)
                
                query = args.analyze if args.analyze else f"Playbook: {path}"
                try:
                    status_context.start()
                    self.app.services.ai.analyze_execution_results(
                        results_all,
                        query=query,
                        chunk_callback=callback
                    )
                    if first_chunk:
                        try: status_context.stop()
                        except: pass
                        printer.console.print(Rule(title="[architect][bold]Network Architect AI Playbook Analysis[/bold][/architect]", style="architect"))
                    renderer.flush()
                    printer.console.print(Rule(style="architect"))
                except Exception as e:
                    printer.error(f"AI Analysis failed: {e}")

        except Exception as e:
            printer.error(f"Failed to run playbook {path}: {e}")
            sys.exit(10)

    def cli_run(self, script):
        name = script.get("name", "Task")
        try:
            action = script["action"]
            nodelist = script["nodes"]
            commands = script["commands"]
            variables = script.get("variables")
            output_cfg = script["output"]
            options = script.get("options", {})
        except KeyError as e:
            printer.error(f"[{name}] '{e.args[0]}' is mandatory in script")
            sys.exit(11)

        stdout = (output_cfg == "stdout")
        folder = output_cfg if output_cfg not in [None, "stdout"] else None
        prompt = options.get("prompt")

        # Resolve and filter nodes through context-aware list_nodes
        try:
            if isinstance(nodelist, str):
                resolved_nodes = self.app.services.nodes.list_nodes(nodelist)
            elif isinstance(nodelist, list):
                resolved_nodes = []
                for item in nodelist:
                    matches = self.app.services.nodes.list_nodes(item)
                    for m in matches:
                        if m not in resolved_nodes:
                            resolved_nodes.append(m)
            else:
                resolved_nodes = []
        except Exception:
            resolved_nodes = []

        if not resolved_nodes:
            printer.error(f"[{name}] No nodes found matching filter: {nodelist}")
            sys.exit(11)

        nodelist = resolved_nodes

        results = {}
        try:
            header_printed = False
            if action == "run":
                # If stdout is true, we stream results as they arrive
                def _on_run_complete(unique, node_output, node_status):
                    nonlocal header_printed
                    if stdout:
                        with self.print_lock:
                            if not header_printed:
                                printer.console.print(Rule(name.upper(), style="header"))
                                header_printed = True
                        printer.node_panel(unique, node_output, node_status)

                results = self.app.services.execution.run_commands(
                    nodes_filter=nodelist,
                    commands=commands,
                    variables=variables,
                    parallel=options.get("parallel", 10),
                    timeout=options.get("timeout", 20),
                    folder=folder,
                    prompt=prompt,
                    on_node_complete=_on_run_complete
                )
                # Final Summary
                if not stdout and not folder:
                    with self.print_lock:
                        printer.console.print(Rule(name.upper(), style="header"))
                    for unique, data in results.items():
                        output = data["output"] if isinstance(data, dict) else data
                        printer.node_panel(unique, output, 0)
                
                # ALWAYS show the aggregate execution summary at the end
                printer.run_summary(results)

            elif action == "test":
                expected = script.get("expected", [])
                # Show test_panel per node ONLY if stdout is True
                def _on_test_complete(unique, node_output, node_status, node_result):
                    nonlocal header_printed
                    if stdout:
                        with self.print_lock:
                            if not header_printed:
                                printer.console.print(Rule(name.upper(), style="header"))
                                header_printed = True
                        printer.test_panel(unique, node_output, node_status, node_result)

                results = self.app.services.execution.test_commands(
                    nodes_filter=nodelist,
                    commands=commands,
                    expected=expected,
                    variables=variables,
                    parallel=options.get("parallel", 10),
                    timeout=options.get("timeout", 20),
                    folder=folder,
                    prompt=prompt,
                    on_node_complete=_on_test_complete
                )
                # ALWAYS show the aggregate summary at the end
                printer.test_summary(results)
                
            return results

        except ConnpyError as e:
            printer.error(str(e))
            return {}

    def ai_generate(self, args):
        from rich.prompt import Prompt
        from rich.rule import Rule
        from rich.panel import Panel
        from rich.syntax import Syntax

        dest_file = args.data[0]
        if os.path.exists(dest_file):
            printer.error(f"File '{dest_file}' already exists.")
            sys.exit(14)

        chat_history = []
        
        # Consistent layout opening matching global AI (engineer style)
        from rich.markdown import Markdown
        printer.console.print(Rule(style="engineer"))
        printer.console.print(Markdown("**Playbook Builder AI**: Welcome! Describe the automation workflow you want to design.\nType **exit** to quit.\n"))
        printer.console.print(Rule(style="engineer"))
        
        while True:
            try:
                user_prompt = Prompt.ask("[user_prompt]User[/user_prompt]")
            except (KeyboardInterrupt, EOFError):
                printer.console.print()
                printer.warning("Operation cancelled by user.")
                break
                
            if user_prompt.strip().lower() in ["exit", "quit"]:
                printer.info("Exiting AI Assistant.")
                break
                
            if not user_prompt.strip():
                continue
                
            printer.console.print()
            
            renderer = printer.BlockMarkdownRenderer()
            first_chunk = True
            status_context = printer.console.status("[ai_status]Agent is thinking...[/ai_status]")
            
            def callback(chunk):
                nonlocal first_chunk
                if first_chunk:
                    try:
                        status_context.stop()
                    except:
                        pass
                    printer.console.print(Rule(title="[engineer][bold]Playbook Builder AI[/bold][/engineer]", style="engineer"))
                    first_chunk = False
                renderer.feed(chunk)
                
            try:
                status_context.start()
                res = self.app.services.ai.build_playbook_chat(
                    user_prompt,
                    chat_history=chat_history,
                    chunk_callback=callback
                )
                if first_chunk:
                    try:
                        status_context.stop()
                    except:
                        pass
                renderer.flush()
                if not first_chunk:
                    printer.console.print(Rule(style="engineer"))
                
                # Update history
                if res and "chat_history" in res:
                    chat_history = res["chat_history"]
                
                # Check if the agent returned a validated playbook YAML
                if res and "playbook_yaml" in res and res["playbook_yaml"]:
                    yaml_content = res["playbook_yaml"]
                    printer.console.print()
                    printer.success("Playbook YAML successfully generated and validated.")
                    
                    # Show the YAML inside a beautiful panel matching AI style (with engineer borders)
                    syntax = Syntax(yaml_content, "yaml", theme="ansi_dark", word_wrap=True, background_color="default")
                    panel = Panel(syntax, title="[engineer][bold]Resulting Playbook[/bold][/engineer]", border_style="engineer", expand=False)
                    printer.console.print(panel)
                    
                    # Ask if the user wants to save it
                    try:
                        save_confirm = Prompt.ask(
                            f"\nDo you want to save this playbook to '{dest_file}'?",
                            choices=["y", "n", "run"],
                            default="y"
                        )
                    except (KeyboardInterrupt, EOFError):
                        printer.console.print()
                        printer.warning("Saving skipped.")
                        break
                        
                    choice = save_confirm.strip().lower()
                    if choice in ["y", "yes", "run"]:
                        with open(dest_file, "w") as f:
                            f.write(yaml_content)
                        printer.success(f"Playbook saved successfully to '{dest_file}'")
                        if choice == "run":
                            printer.console.print()
                            printer.info("Executing the saved playbook...")
                            self.yaml_run(args)
                        break
                    else:
                        printer.warning("Playbook not saved. You can continue describing changes or exit.")
            except Exception as e:
                printer.error(f"Error in AI chat: {e}")
