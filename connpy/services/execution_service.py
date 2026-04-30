from typing import List, Dict, Any, Callable, Optional
import os
import yaml
from .base import BaseService
from connpy.core import nodes as Nodes
from .exceptions import ConnpyError

class ExecutionService(BaseService):
    """Business logic for executing commands on nodes and running automation scripts."""

    def run_commands(
        self, 
        nodes_filter: str, 
        commands: List[str], 
        variables: Optional[Dict[str, Any]] = None,
        parallel: int = 10,
        timeout: int = 10,
        folder: Optional[str] = None,
        prompt: Optional[str] = None,
        on_node_complete: Optional[Callable] = None,
        logger: Optional[Callable] = None,
        name: Optional[str] = None
    ) -> Dict[str, str]:

        """Execute commands on a set of nodes."""
        try:
            matched_names = self.config._getallnodes(nodes_filter)
            if not matched_names:
                raise ConnpyError(f"No nodes found matching filter: {nodes_filter}")
            
            node_data = self.config.getitems(matched_names, extract=True)
            executor = Nodes(node_data, config=self.config)
            self.last_executor = executor
            
            results = executor.run(
                commands=commands,
                vars=variables,
                parallel=parallel,
                timeout=timeout,
                folder=folder,
                prompt=prompt,
                on_complete=on_node_complete,
                logger=logger
            )

            # Combine output and status for the caller
            full_results = {}
            for unique in results:
                full_results[unique] = {
                    "output": results[unique],
                    "status": executor.status.get(unique, 1)
                }

            return full_results
        except Exception as e:
            raise ConnpyError(f"Execution failed: {e}")

    def test_commands(
        self,
        nodes_filter: str,
        commands: List[str],
        expected: List[str],
        variables: Optional[Dict[str, Any]] = None,
        parallel: int = 10,
        timeout: int = 10,
        folder: Optional[str] = None,
        prompt: Optional[str] = None,
        on_node_complete: Optional[Callable] = None,
        logger: Optional[Callable] = None,
        name: Optional[str] = None
    ) -> Dict[str, Dict[str, bool]]:

        """Run commands and verify expected output on a set of nodes."""
        try:
            matched_names = self.config._getallnodes(nodes_filter)
            if not matched_names:
                raise ConnpyError(f"No nodes found matching filter: {nodes_filter}")
            
            node_data = self.config.getitems(matched_names, extract=True)
            executor = Nodes(node_data, config=self.config)
            self.last_executor = executor
            
            results = executor.test(
                commands=commands,
                expected=expected,
                vars=variables,
                parallel=parallel,
                timeout=timeout,
                folder=folder,
                prompt=prompt,
                on_complete=on_node_complete,
                logger=logger
            )
            return results
        except Exception as e:
            raise ConnpyError(f"Testing failed: {e}")

    def run_cli_script(self, nodes_filter: str, script_path: str, parallel: int = 10) -> Dict[str, str]:
        """Run a plain-text script containing one command per line."""
        if not os.path.exists(script_path):
            raise ConnpyError(f"Script file not found: {script_path}")
            
        try:
            with open(script_path, "r") as f:
                commands = [line.strip() for line in f if line.strip()]
        except Exception as e:
            raise ConnpyError(f"Failed to read script {script_path}: {e}")
            
        return self.run_commands(nodes_filter, commands, parallel=parallel)

    def run_yaml_playbook(self, playbook_data: str, parallel: int = 10) -> Dict[str, Any]:
        """Run a structured Connpy YAML automation playbook (from path or content)."""
        playbook = None
        if playbook_data.startswith("---YAML---\n"):
            try:
                content = playbook_data[len("---YAML---\n"):]
                playbook = yaml.load(content, Loader=yaml.FullLoader)
            except Exception as e:
                raise ConnpyError(f"Failed to parse YAML content: {e}")
        else:
            if not os.path.exists(playbook_data):
                raise ConnpyError(f"Playbook file not found: {playbook_data}")
            try:
                with open(playbook_data, "r") as f:
                    playbook = yaml.load(f, Loader=yaml.FullLoader)
            except Exception as e:
                raise ConnpyError(f"Failed to load playbook {playbook_data}: {e}")
            
        # Basic validation
        if not isinstance(playbook, dict) or "nodes" not in playbook or "commands" not in playbook:
            raise ConnpyError("Invalid playbook format: missing 'nodes' or 'commands' keys.")
            
        action = playbook.get("action", "run")
        options = playbook.get("options", {})
        
        # Extract all fields similar to RunHandler.cli_run
        exec_args = {
            "nodes_filter": playbook["nodes"],
            "commands": playbook["commands"],
            "variables": playbook.get("variables"),
            "parallel": options.get("parallel", parallel),
            "timeout": playbook.get("timeout", options.get("timeout", 10)),
            "prompt": options.get("prompt"),
            "name": playbook.get("name", "Task")
        }

        # Map 'output' field to folder path if it's not stdout/null
        output_cfg = playbook.get("output")
        if output_cfg not in [None, "stdout"]:
            exec_args["folder"] = output_cfg

        if action == "run":
            return self.run_commands(**exec_args)
        elif action == "test":
            exec_args["expected"] = playbook.get("expected", [])
            return self.test_commands(**exec_args)
        else:
            raise ConnpyError(f"Unsupported playbook action: {action}")

