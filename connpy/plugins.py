#!/usr/bin/python3
import ast
import importlib.util
import sys
import argparse
import os
from connpy import printer

class Plugins:
    def __init__(self):
        self.plugins = {}
        self.plugin_parsers = {}
        self.preloads = {}
        self.remote_plugins = {}
        self.preferences = {}

    def _load_preferences(self, config_dir):
        import json
        path = os.path.join(config_dir, "plugin_preferences.json")
        try:
            with open(path) as f:
                self.preferences = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.preferences = {}

    def _save_preferences(self, config_dir):
        import json
        path = os.path.join(config_dir, "plugin_preferences.json")
        try:
            with open(path, "w") as f:
                json.dump(self.preferences, f, indent=4)
        except OSError as e:
            printer.error(f"Failed to save plugin preferences: {e}")


    def verify_script(self, file_path):
        """
        Verifies that a given Python script meets specific structural requirements.

        This function checks a Python script for compliance with predefined structural 
        rules. It ensures that the script contains only allowed top-level elements 
        (functions, classes, imports, pass statements, and a specific if __name__ block) 
        and that it includes mandatory classes with specific attributes and methods.

        ### Arguments:
            - file_path (str): The file path of the Python script to be verified.

        ### Returns:
            - str: A message indicating the type of violation if the script doesn't meet 
                 the requirements, or False if all requirements are met.

        ### Verifications:
            - The presence of only allowed top-level elements.
            - The existence of two specific classes: 'Parser' and 'Entrypoint'. and/or specific class: Preload.
            - 'Parser' class must only have an '__init__' method and must assign 'self.parser'.
            - 'Entrypoint' class must have an '__init__' method accepting specific arguments.

        If any of these checks fail, the function returns an error message indicating 
        the reason. If the script passes all checks, the function returns False, 
        indicating successful verification.

        ### Exceptions:
                - SyntaxError: If the script contains a syntax error, it is caught and 
                               returned as a part of the error message.
        """
        with open(file_path, 'r') as file:
            source_code = file.read()

        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            return f"Syntax error in file: {e}"


        has_parser = False
        has_entrypoint = False
        has_preload = False

        for node in tree.body:
            # Allow only function definitions, class definitions, and pass statements at top-level
            if isinstance(node, ast.If):
                # Check for the 'if __name__ == "__main__":' block
                if not (isinstance(node.test, ast.Compare) and
                        isinstance(node.test.left, ast.Name) and
                        node.test.left.id == '__name__' and
                        ((hasattr(ast, 'Str') and isinstance(node.test.comparators[0], getattr(ast, 'Str')) and node.test.comparators[0].s == '__main__') or
                         (hasattr(ast, 'Constant') and isinstance(node.test.comparators[0], getattr(ast, 'Constant')) and node.test.comparators[0].value == '__main__'))):
                    return "Only __name__ == __main__ If is allowed"

            elif not isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom, ast.Pass)):
                return f"Plugin can only have pass, functions, classes and imports. {node} is not allowed"  # Reject any other AST types

            if isinstance(node, ast.ClassDef):

                if node.name == 'Parser':
                    has_parser = True
                    # Ensure Parser class has only the __init__ method and assigns self.parser
                    if not all(isinstance(method, ast.FunctionDef) and method.name == '__init__' for method in node.body):
                        return "Parser class should only have __init__ method"

                    # Check if 'self.parser' is assigned in __init__ method
                    init_method = node.body[0]
                    assigned_attrs = [target.attr for expr in init_method.body if isinstance(expr, ast.Assign) for target in expr.targets if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self']
                    if 'parser' not in assigned_attrs:
                        return "Parser class should set self.parser"


                elif node.name == 'Entrypoint':
                    has_entrypoint = True
                    init_method = next((item for item in node.body if isinstance(item, ast.FunctionDef) and item.name == '__init__'), None)
                    if not init_method or len(init_method.args.args) != 4:  # self, args, parser, conapp
                        return "Entrypoint class should have method __init__ and accept only arguments: args, parser and connapp"  # 'Entrypoint' __init__ does not have correct signature

                elif node.name == 'Preload':
                    has_preload = True
                    init_method = next((item for item in node.body if isinstance(item, ast.FunctionDef) and item.name == '__init__'), None)
                    if not init_method or len(init_method.args.args) != 2:  # self, connapp
                        return "Preload class should have method __init__ and accept only argument: connapp"  # 'Preload' __init__ does not have correct signature

        # Applying the combination logic based on class presence
        if has_parser and not has_entrypoint:
            return "Parser requires Entrypoint class to be present."
        elif has_entrypoint and not has_parser:
            return "Entrypoint requires Parser class to be present."
    
        if not (has_parser or has_entrypoint or has_preload):
            return "No valid class (Parser, Entrypoint, or Preload) found."

        return False  # All requirements met, no error

    def _import_from_path(self, path):
        spec = importlib.util.spec_from_file_location("module.name", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["module.name"] = module
        spec.loader.exec_module(module)
        return module

    def _import_plugins_to_argparse(self, directory, subparsers, remote_enabled=False):
        if not os.path.exists(directory):
            return
        for filename in os.listdir(directory):
            commands = subparsers.choices.keys()
            if filename.endswith(".py"):
                root_filename = os.path.splitext(filename)[0]
                if root_filename in commands:
                    continue
                
                # Check preferences: if remote is preferred AND remote is enabled, skip local loading
                if remote_enabled and self.preferences.get(root_filename) == "remote":
                    continue

                # Construct the full path
                filepath = os.path.join(directory, filename)
                check_file = self.verify_script(filepath)
                if check_file:
                    printer.error(f"Failed to load plugin: {filename}. Reason: {check_file}")
                    continue
                else:
                    self.plugins[root_filename] = self._import_from_path(filepath)
                    if hasattr(self.plugins[root_filename], "Parser"):
                        self.plugin_parsers[root_filename] = self.plugins[root_filename].Parser()
                        plugin = self.plugin_parsers[root_filename]
                        # Default to RichHelpFormatter if plugin doesn't set one
                        try:
                            from rich_argparse import RichHelpFormatter as _RHF
                            fmt = plugin.parser.formatter_class
                            if fmt is argparse.HelpFormatter or fmt is argparse.RawTextHelpFormatter or fmt is argparse.RawDescriptionHelpFormatter:
                                fmt = _RHF
                        except ImportError:
                            fmt = plugin.parser.formatter_class
                        subparsers.add_parser(root_filename, parents=[self.plugin_parsers[root_filename].parser], add_help=False, help=plugin.parser.description, usage=plugin.parser.usage, description=plugin.parser.description, epilog=plugin.parser.epilog, formatter_class=fmt)
                    if hasattr(self.plugins[root_filename], "Preload"):
                        self.preloads[root_filename] = self.plugins[root_filename]

    def _import_remote_plugins_to_argparse(self, plugin_stub, subparsers, cache_dir, force_sync=False):
        import hashlib
        os.makedirs(cache_dir, exist_ok=True)
        
        try:
            remote_plugins_info = plugin_stub.list_plugins()
        except Exception:
            return

        # Pruning: Remove local cached files that are no longer on the server
        for local_file in os.listdir(cache_dir):
            if local_file.endswith(".py"):
                name = local_file[:-3]
                if name not in remote_plugins_info:
                    try:
                        os.remove(os.path.join(cache_dir, local_file))
                    except Exception:
                        pass

        for name, info in remote_plugins_info.items():
            if not info.get("enabled", True):
                continue
                
            pref = self.preferences.get(name, "local")
            if pref != "remote" and name in self.plugins:
                continue
            if not force_sync and name in subparsers.choices:
                continue

            cache_path = os.path.join(cache_dir, f"{name}.py")
            
            # Hash comparison
            remote_hash = info.get("hash", "")
            local_hash = ""
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "rb") as f:
                        local_hash = hashlib.md5(f.read()).hexdigest()
                except Exception:
                    pass

            # Update only if hash differs or force_sync is True
            if force_sync or remote_hash != local_hash or not os.path.exists(cache_path):
                try:
                    source = plugin_stub.get_plugin_source(name)
                    with open(cache_path, "w") as f:
                        f.write(source)
                except Exception as e:
                    printer.warning(f"Failed to sync remote plugin {name}: {e}")
                    continue

            # Verify and load
            check_file = self.verify_script(cache_path)
            if check_file:
                printer.warning(f"Remote plugin {name} failed verification: {check_file}")
                continue

            module = self._import_from_path(cache_path)
            if hasattr(module, "Parser"):
                self.plugin_parsers[name] = module.Parser()
                self.remote_plugins[name] = True
                plugin = self.plugin_parsers[name]
                try:
                    from rich_argparse import RichHelpFormatter as _RHF
                    fmt = plugin.parser.formatter_class
                    if fmt is argparse.HelpFormatter or fmt is argparse.RawTextHelpFormatter or fmt is argparse.RawDescriptionHelpFormatter:
                        fmt = _RHF
                except ImportError:
                    fmt = plugin.parser.formatter_class
                
                # If force_sync, we might be re-registering, but argparse subparsers.add_parser 
                # might fail if it exists. We check if it's already there.
                if name not in subparsers.choices:
                    subparsers.add_parser(
                        name, 
                        parents=[plugin.parser], 
                        add_help=False, 
                        help=f"[remote] {plugin.parser.description}", 
                        usage=plugin.parser.usage, 
                        description=plugin.parser.description, 
                        epilog=plugin.parser.epilog, 
                        formatter_class=fmt
                    )
