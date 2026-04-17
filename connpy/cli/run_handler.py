import os
import sys
import yaml
from rich.rule import Rule
from .. import printer
from ..services.exceptions import ConnpyError
from .help_text import get_instructions

class RunHandler:
    def __init__(self, app):
        self.app = app

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
            # Inline execution with streaming results
            def _on_node_complete(unique, node_output, node_status):
                nonlocal header_printed
                if not header_printed:
                    printer.console.print(Rule("OUTPUT", style="header"))
                    header_printed = True
                printer.node_panel(unique, node_output, node_status)
                
            self.app.services.execution.run_commands(
                nodes_filter=nodes_filter,
                commands=commands,
                on_node_complete=_on_node_complete
            )

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
        try:
            action = script["action"]
            nodelist = script["nodes"]
            commands = script["commands"]
            variables = script.get("variables")
            output_cfg = script["output"]
            name = script.get("name", "Task")
            options = script.get("options", {})
        except KeyError as e:
            printer.error(f"'{e.args[0]}' is mandatory in script")
            sys.exit(11)

        stdout = (output_cfg == "stdout")
        folder = output_cfg if output_cfg not in [None, "stdout"] else None
        prompt = options.get("prompt")
        printer.header(name.upper())
        
        try:
            if action == "run":
                # If stdout is true, we stream results as they arrive
                on_complete = printer.node_panel if stdout else None
                results = self.app.services.execution.run_commands(
                    nodes_filter=nodelist,
                    commands=commands,
                    variables=variables,
                    parallel=options.get("parallel", 10),
                    timeout=options.get("timeout", 10),
                    folder=folder,
                    prompt=prompt,
                    on_node_complete=on_complete
                )
                # If not streaming, we could print a summary table here if needed
                if not stdout:
                    for unique, output in results.items():
                        printer.node_panel(unique, output, 0)
                        
            elif action == "test":
                expected = script.get("expected", [])
                on_complete = printer.test_panel if stdout else None
                results = self.app.services.execution.test_commands(
                    nodes_filter=nodelist,
                    commands=commands,
                    expected=expected,
                    variables=variables,
                    parallel=options.get("parallel", 10),
                    timeout=options.get("timeout", 10),
                    prompt=prompt,
                    on_node_complete=on_complete
                )
                if not stdout:
                    printer.test_summary(results)
                
        except ConnpyError as e:
            printer.error(str(e))
