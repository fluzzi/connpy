#!/usr/bin/python3
import ast
import importlib.util
import sys
import argparse
import os

class Plugins:
    def __init__(self):
        self.plugins = {}
        self.plugin_parsers = {}
        self.preloads = {}

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
            - 'Parser' class must only have an '__init__' method and must assign 'self.parser'
              and 'self.description'.
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
                        isinstance(node.test.comparators[0], ast.Str) and
                        node.test.comparators[0].s == '__main__'):
                    return "Only __name__ == __main__ If is allowed"

            elif not isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom, ast.Pass)):
                return f"Plugin can only have pass, functions, classes and imports. {node} is not allowed"  # Reject any other AST types

            if isinstance(node, ast.ClassDef):

                if node.name == 'Parser':
                    has_parser = True
                    # Ensure Parser class has only the __init__ method and assigns self.parser
                    if not all(isinstance(method, ast.FunctionDef) and method.name == '__init__' for method in node.body):
                        return "Parser class should only have __init__ method"

                    # Check if 'self.parser' and 'self.description' are assigned in __init__ method
                    init_method = node.body[0]
                    assigned_attrs = [target.attr for expr in init_method.body if isinstance(expr, ast.Assign) for target in expr.targets if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self']
                    if 'parser' not in assigned_attrs or 'description' not in assigned_attrs:
                        return "Parser class should set self.parser and self.description" # 'self.parser' or 'self.description' not assigned in __init__

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

    def _import_plugins_to_argparse(self, directory, subparsers):
        for filename in os.listdir(directory):
            commands = subparsers.choices.keys()
            if filename.endswith(".py"):
                root_filename = os.path.splitext(filename)[0]
                if root_filename in commands:
                    continue
                # Construct the full path
                filepath = os.path.join(directory, filename)
                check_file = self.verify_script(filepath)
                if check_file:
                    print(f"Failed to load plugin: {filename}. Reason: {check_file}")
                    continue
                else:
                    self.plugins[root_filename] = self._import_from_path(filepath)
                    if hasattr(self.plugins[root_filename], "Parser"):
                        self.plugin_parsers[root_filename] = self.plugins[root_filename].Parser()
                        subparsers.add_parser(root_filename, parents=[self.plugin_parsers[root_filename].parser], add_help=False, description=self.plugin_parsers[root_filename].description)
                    if hasattr(self.plugins[root_filename], "Preload"):
                        self.preloads[root_filename] = self.plugins[root_filename]

