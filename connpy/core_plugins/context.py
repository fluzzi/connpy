import argparse
import yaml
import re


class context_manager:

    def __init__(self, connapp):
        self.connapp = connapp
        self.config = connapp.config
        self.contexts = self.config.config["contexts"]
        self.current_context = self.config.config["current_context"]
        self.regex = [re.compile(regex) for regex in self.contexts[self.current_context]]

    def add_context(self, context, regex):
        if not context.isalnum():
            print("Context name has to be alphanumeric.")
            exit(1)
        elif context in self.contexts:
            print(f"Context {context} already exists.")
            exit(2)
        else:
            self.contexts[context] = regex
            self.connapp._change_settings("contexts", self.contexts)

    def modify_context(self, context, regex):
        if context == "all":
            print("Can't modify default context: all")
            exit(3)
        elif context not in self.contexts:
            print(f"Context {context} doesn't exist.")
            exit(4)
        else:
            self.contexts[context] = regex
            self.connapp._change_settings("contexts", self.contexts)

    def delete_context(self, context):
        if context == "all":
            print("Can't delete default context: all")
            exit(3)
        elif context not in self.contexts:
            print(f"Context {context} doesn't exist.")
            exit(4)
        if context == self.current_context:
            print(f"Can't delete current context: {self.current_context}")
            exit(5)
        else:
            self.contexts.pop(context)
            self.connapp._change_settings("contexts", self.contexts)

    def list_contexts(self):
        for key in self.contexts.keys():
            if key == self.current_context:
                print(f"{key} * (active)")
            else:
                print(key)

    def set_context(self, context):
        if context not in self.contexts:
            print(f"Context {context} doesn't exist.")
            exit(4)
        elif context == self.current_context:
            print(f"Context {context} already set")
            exit(0)
        else:
            self.connapp._change_settings("current_context", context)

    def show_context(self, context):
        if context not in self.contexts:
            print(f"Context {context} doesn't exist.")
            exit(4)
        else:
            yaml_output = yaml.dump(self.contexts[context], sort_keys=False, default_flow_style=False)
            print(yaml_output)


    @staticmethod
    def add_default_context(config):
        config_modified = False
        if "contexts" not in config.config:
            config.config["contexts"]  = {}
            config.config["contexts"]["all"] = [".*"]
            config_modified = True
        if "current_context" not in config.config:
            config.config["current_context"] = "all"
            config_modified = True
        if config_modified:
            config._saveconfig(config.file)

    def match_any_regex(self, node, regex_list):
        return any(regex.match(node) for regex in regex_list)

    def modify_node_list(self, *args, **kwargs):
        filtered_nodes = [node for node in kwargs["result"] if self.match_any_regex(node, self.regex)]
        return filtered_nodes

    def modify_node_dict(self, *args, **kwargs):
        filtered_nodes = {key: value for key, value in kwargs["result"].items() if self.match_any_regex(key, self.regex)}
        return filtered_nodes

class Preload:
    def __init__(self, connapp):
        #define contexts if doesn't exist
        connapp.config.modify(context_manager.add_default_context)
        #filter nodes using context
        cm = context_manager(connapp)
        connapp.nodes_list = [node for node in connapp.nodes_list if cm.match_any_regex(node, cm.regex)]
        connapp.folders = [node for node in connapp.folders if cm.match_any_regex(node, cm.regex)]
        connapp.config._getallnodes.register_post_hook(cm.modify_node_list)
        connapp.config._getallfolders.register_post_hook(cm.modify_node_list)
        connapp.config._getallnodesfull.register_post_hook(cm.modify_node_dict)

class Parser:
    def __init__(self):
        self.parser = argparse.ArgumentParser(description="Manage contexts with regex matching", formatter_class=argparse.RawTextHelpFormatter)
        self.description = "Manage contexts with regex matching"
        
        # Define the context name as a positional argument
        self.parser.add_argument("context_name", help="Name of the context", nargs='?')

        group = self.parser.add_mutually_exclusive_group(required=True)
        group.add_argument("-a", "--add", nargs='+', help='Add a new context with regex values. Usage: context -a name "regex1" "regex2"')
        group.add_argument("-r", "--rm", "--del", action='store_true', help="Delete a context. Usage: context -d name")
        group.add_argument("--ls", action='store_true', help="List all contexts. Usage: context --list")
        group.add_argument("--set", action='store_true', help="Set the used context. Usage: context --set name")
        group.add_argument("-s", "--show", action='store_true', help="Show the defined regex of a context. Usage: context --show name")
        group.add_argument("-e", "--edit", "--mod", nargs='+', help='Modify an existing context. Usage: context --mod name "regex1" "regex2"')

class Entrypoint:
    def __init__(self, args, parser, connapp):
        if args.add and len(args.add) < 2:
            parser.error("--add requires at least 2 arguments: name and at least one regex")
        if args.edit and len(args.edit) < 2:
            parser.error("--edit requires at least 2 arguments: name and at least one regex")
        if args.ls and args.context_name is not None:
            parser.error("--ls does not require a context name")
        if args.rm and not args.context_name:
            parser.error("--rm require a context name")
        if args.set and not args.context_name:
            parser.error("--set require a context name")
        if args.show and not args.context_name:
            parser.error("--show require a context name")

        cm = context_manager(connapp)

        if args.add:
            cm.add_context(args.add[0], args.add[1:])
        elif args.rm:
            cm.delete_context(args.context_name)
        elif args.ls:
            cm.list_contexts()
        elif args.edit:
            cm.modify_context(args.edit[0], args.edit[1:])
        elif args.set:
            cm.set_context(args.context_name)
        elif args.show:
            cm.show_context(args.context_name)

def _connpy_completion(wordsnumber, words, info=None):
    if wordsnumber == 3:
        result = ["--help", "--add", "--del", "--rm", "--ls", "--set", "--show", "--edit", "--mod"]
    elif wordsnumber == 4 and words[1] in ["--del", "-r", "--rm", "--set", "--edit", "--mod", "-e", "--show", "-s"]:
        contexts = info["config"]["config"]["contexts"].keys()
        current_context = info["config"]["config"]["current_context"]
        default_context = "all"
        
        if words[1] in ["--del", "-r", "--rm"]:
            # Filter out default context and current context
            result = [context for context in contexts if context not in [default_context, current_context]]
        elif words[1] == "--set":
            # Filter out current context
            result = [context for context in contexts if context != current_context]
        elif words[1] in ["--edit", "--mod", "-e"]:
            # Filter out default context
            result = [context for context in contexts if context != default_context]
        elif words[1] in ["--show", "-s"]:
            # No filter for show
            result = list(contexts)
    
    return result
