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
        actions = {"noderun": self.node_run, "generate": self.yaml_generate, "run": self.yaml_run}
        return actions.get(args.action)(args)

    def node_run(self, args):
        nodes_filter = args.data[0]
        commands = [" ".join(args.data[1:])]

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
                    nodes_filter=nodes_filter,
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
                    nodes_filter=nodes_filter,
                    commands=commands,
                    on_node_complete=_on_node_complete
                )
                printer.run_summary(results)

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

            for task in playbook.get("tasks", []):
                self.cli_run(task)

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
                    timeout=options.get("timeout", 10),
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
                    timeout=options.get("timeout", 10),
                    folder=folder,
                    prompt=prompt,
                    on_node_complete=_on_test_complete
                )
                # ALWAYS show the aggregate summary at the end
                printer.test_summary(results)

        except ConnpyError as e:
            printer.error(str(e))
