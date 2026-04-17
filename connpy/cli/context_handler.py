import sys
import yaml
from .. import printer
from ..services.exceptions import ConnpyError

class ContextHandler:
    def __init__(self, app):
        self.app = app
        self.service = self.app.services.context

    def dispatch(self, args):
        try:
            if args.add:
                if len(args.add) < 2:
                    printer.error("--add requires name and at least one regex")
                    return
                self.service.add_context(args.add[0], args.add[1:])
                printer.success(f"Context '{args.add[0]}' added successfully.")
            
            elif args.rm:
                if not args.context_name:
                    printer.error("--rm requires a context name")
                    return
                self.service.delete_context(args.context_name)
                printer.success(f"Context '{args.context_name}' deleted successfully.")
            
            elif args.ls:
                contexts = self.service.list_contexts()
                for ctx in contexts:
                    if ctx["active"]:
                        printer.success(f"{ctx['name']} (active)")
                    else:
                        printer.custom(" ", ctx["name"])
            
            elif args.set:
                if not args.context_name:
                    printer.error("--set requires a context name")
                    return
                self.service.set_active_context(args.context_name)
                printer.success(f"Context set to: {args.context_name}")
            
            elif args.show:
                if not args.context_name:
                    printer.error("--show requires a context name")
                    return
                contexts = self.service.contexts
                if args.context_name not in contexts:
                    printer.error(f"Context '{args.context_name}' does not exist")
                    return
                yaml_output = yaml.dump(contexts[args.context_name], sort_keys=False, default_flow_style=False)
                printer.custom(args.context_name, "")
                print(yaml_output)
            
            elif args.edit:
                if len(args.edit) < 2:
                    printer.error("--edit requires name and at least one regex")
                    return
                self.service.update_context(args.edit[0], args.edit[1:])
                printer.success(f"Context '{args.edit[0]}' modified successfully.")
            
            else:
                # Default behavior if no flags: show list
                self.dispatch_ls(args)

        except ValueError as e:
            printer.error(str(e))
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)

    def dispatch_ls(self, args):
        contexts = self.service.list_contexts()
        for ctx in contexts:
            if ctx["active"]:
                printer.success(f"{ctx['name']} (active)")
            else:
                printer.custom(" ", ctx["name"])
