import sys
import yaml
import inquirer
from rich.markdown import Markdown

from .. import printer
from ..services.exceptions import ConnpyError, InvalidConfigurationError
from .helpers import choose
from .forms import Forms
from .help_text import get_instructions

class NodeHandler:
    def __init__(self, app):
        self.app = app
        self.forms = Forms(app)

    def dispatch(self, args):
        if not self.app.case and args.data != None:
            args.data = args.data.lower()
        actions = {"version": self.version, "connect": self.connect, "add": self.add, "del": self.delete, "mod": self.modify, "show": self.show}
        return actions.get(args.action)(args)

    def version(self, args):
        from .._version import __version__
        printer.info(f"Connpy {__version__}")

    def connect(self, args):
        if args.data == None:
            try:
                matches = self.app.services.nodes.list_nodes()
            except Exception as e:
                printer.error(f"Failed to list nodes: {e}")
                sys.exit(1)
                
            if len(matches) == 0:
                printer.warning("There are no nodes created")
                printer.info("try: connpy --help")
                sys.exit(9)
        else:
            try:
                matches = self.app.services.nodes.list_nodes(args.data)
            except Exception:
                matches = []

        if len(matches) == 0:
            printer.error(f"{args.data} not found")
            sys.exit(2)
        elif len(matches) > 1:
            matches[0] = choose(self.app, matches, "node", "connect")
            
        if matches[0] == None:
            sys.exit(7)
            
        try:
            self.app.services.nodes.connect_node(
                matches[0], 
                sftp=args.sftp, 
                debug=args.debug, 
                logger=self.app._service_logger
            )
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)

    def delete(self, args):
        if args.data == None:
            printer.error("Missing argument node")
            sys.exit(3)
        
        is_folder = args.data.startswith("@")
        try:
            if is_folder:
                matches = self.app.services.nodes.list_folders(args.data)
            else:
                matches = self.app.services.nodes.list_nodes(args.data)
        except Exception:
            matches = []

        if len(matches) == 0:
            printer.error(f"{args.data} not found")
            sys.exit(2)

        printer.info(f"Removing: {matches}")
        question = [inquirer.Confirm("delete", message="Are you sure you want to continue?")]
        confirm = inquirer.prompt(question)
        if confirm == None or not confirm["delete"]:
            sys.exit(7)

        try:
            for item in matches:
                self.app.services.nodes.delete_node(item, is_folder=is_folder)
            
            if len(matches) == 1:
                printer.success(f"{matches[0]} deleted successfully")
            else:
                printer.success(f"{len(matches)} items deleted successfully")
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)

    def add(self, args):
        try:
            args.data = self.app._type_node(args.data)
        except ValueError as e:
            printer.error(str(e))
            sys.exit(3)
            
        if args.data == None:
            printer.error("Missing argument node")
            sys.exit(3)
            
        is_folder = args.data.startswith("@")
        try:
            if is_folder:
                uniques = self.app.services.nodes.explode_unique(args.data)
                if not uniques:
                    raise InvalidConfigurationError(f"Invalid folder {args.data}")
                self.app.services.nodes.add_node(args.data, {}, is_folder=True)
                printer.success(f"{args.data} added successfully")
            else:
                if args.data in self.app.nodes_list:
                    printer.error(f"Node '{args.data}' already exists.")
                    sys.exit(1)
                uniques = self.app.services.nodes.explode_unique(args.data)
                printer.console.print(Markdown(get_instructions()))

                new_node_data = self.forms.questions_nodes(args.data, uniques)
                if not new_node_data:
                    sys.exit(7)
                self.app.services.nodes.add_node(args.data, new_node_data)
                printer.success(f"{args.data} added successfully")
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)

    def show(self, args):
        if args.data == None:
            printer.error("Missing argument node")
            sys.exit(3)
            
        try:
            matches = self.app.services.nodes.list_nodes(args.data)
        except Exception:
            matches = []

        if len(matches) == 0:
            printer.error(f"{args.data} not found")
            sys.exit(2)
        elif len(matches) > 1:
            matches[0] = choose(self.app, matches, "node", "show")
            
        if matches[0] == None:
            sys.exit(7)
            
        try:
            node = self.app.services.nodes.get_node_details(matches[0])
            yaml_output = yaml.dump(node, sort_keys=False, default_flow_style=False)
            printer.data(matches[0], yaml_output)
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)

    def modify(self, args):
        if args.data == None:
            printer.error("Missing argument node")
            sys.exit(3)
            
        try:
            matches = self.app.services.nodes.list_nodes(args.data)
        except Exception:
            matches = []
            
        if len(matches) == 0:
            printer.error(f"No connection found with filter: {args.data}")
            sys.exit(2)
            
        unique = matches[0] if len(matches) == 1 else None
        uniques = self.app.services.nodes.explode_unique(unique) if unique else {"id": None, "folder": None}
        
        printer.info(f"Editing: {matches}")
        node_details = {}
        for i in matches:
            node_details[i] = self.app.services.nodes.get_node_details(i)
            
        edits = self.forms.questions_edit()
        if edits == None:
            sys.exit(7)
            
        # Use first match as base for defaults if multiple matches exist
        base_unique = matches[0]
        base_uniques = self.app.services.nodes.explode_unique(base_unique)
        updatenode = self.forms.questions_nodes(base_unique, base_uniques, edit=edits)
        if not updatenode:
            sys.exit(7)
            
        try:
            if len(matches) == 1:
                # Comparison for "Nothing to do"
                current = node_details[matches[0]].copy()
                current.update(uniques)
                current["type"] = "connection"
                if sorted(updatenode.items()) == sorted(current.items()):
                    printer.info("Nothing to do here")
                    return
                self.app.services.nodes.update_node(matches[0], updatenode)
                printer.success(f"{args.data} edited successfully")
            else:
                editcount = 0
                for k in matches:
                    updated_item = self.app.services.nodes.explode_unique(k)
                    updated_item["type"] = "connection"
                    updated_item.update(node_details[k])
                    
                    this_item_changed = False
                    for key, should_edit in edits.items():
                        if should_edit:
                            this_item_changed = True
                            updated_item[key] = updatenode[key]
                    
                    if this_item_changed:
                        editcount += 1
                        self.app.services.nodes.update_node(k, updated_item)
                
                if editcount == 0:
                    printer.info("Nothing to do here")
                else:
                    printer.success(f"{matches} edited successfully")
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)
