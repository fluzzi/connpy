import sys
import yaml
import inquirer

from .. import printer
from ..services.exceptions import ConnpyError, ProfileNotFoundError
from .forms import Forms

class ProfileHandler:
    def __init__(self, app):
        self.app = app
        self.forms = Forms(app)

    def dispatch(self, args):
        if not self.app.case:
            args.data[0] = args.data[0].lower()
        actions = {"add": self.add, "del": self.delete, "mod": self.modify, "show": self.show}
        return actions.get(args.action)(args)

    def delete(self, args):
        name = args.data[0]
        try:
            self.app.services.profiles.get_profile(name)
        except ProfileNotFoundError:
            printer.error(f"{name} not found")
            sys.exit(2)
            
        if name == "default":
            printer.error("Can't delete default profile")
            sys.exit(6)
            
        question = [inquirer.Confirm("delete", message=f"Are you sure you want to delete {name}?")]
        confirm = inquirer.prompt(question)
        if confirm == None or not confirm["delete"]:
            sys.exit(7)
            
        try:
            self.app.services.profiles.delete_profile(name)
            printer.success(f"{name} deleted successfully")
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(8)

    def show(self, args):
        try:
            profile = self.app.services.profiles.get_profile(args.data[0])
            yaml_output = yaml.dump(profile, sort_keys=False, default_flow_style=False)
            printer.data(args.data[0], yaml_output)
        except ProfileNotFoundError:
            printer.error(f"{args.data[0]} not found")
            sys.exit(2)

    def add(self, args):
        name = args.data[0]
        if name in self.app.services.profiles.list_profiles():
            printer.error(f"Profile '{name}' already exists.")
            sys.exit(4)
            
        new_profile_data = self.forms.questions_profiles(name)
        if not new_profile_data:
            sys.exit(7)
            
        try:
            self.app.services.profiles.add_profile(name, new_profile_data)
            printer.success(f"{name} added successfully")
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)

    def modify(self, args):
        name = args.data[0]
        try:
            profile = self.app.services.profiles.get_profile(name, resolve=False)
        except ProfileNotFoundError:
            printer.error(f"Profile '{name}' not found")
            sys.exit(2)
            
        old_profile = {"id": name, **profile}
        edits = self.forms.questions_edit()
        if edits == None:
            sys.exit(7)
            
        update_profile_data = self.forms.questions_profiles(name, edit=edits)
        if not update_profile_data:
            sys.exit(7)
            
        if sorted(update_profile_data.items()) == sorted(old_profile.items()):
            printer.info("Nothing to do here")
            return
            
        try:
            self.app.services.profiles.update_profile(name, update_profile_data)
            printer.success(f"{name} edited successfully")
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)
