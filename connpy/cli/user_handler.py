import sys
import os
import getpass
import yaml
from .. import printer
from ..services.exceptions import ConnpyError

class UserHandler:
    def __init__(self, app):
        self.app = app

    def dispatch(self, args):
        if self.app.services.mode == "remote":
            printer.error("User management commands are only available in local/server-side mode.")
            sys.exit(1)

        # Parse actions from argparse mutually exclusive options
        if getattr(args, "add", None):
            args.action = "add"
            args.username = args.add[0]
        elif getattr(args, "delete", None):
            args.action = "del"
            args.username = args.delete[0]
        elif getattr(args, "list", False):
            args.action = "list"
        elif getattr(args, "show", None):
            args.action = "show"
            args.username = args.show[0]
        elif getattr(args, "regen_password", None):
            args.action = "regen_password"
            args.username = args.regen_password[0]

        action = getattr(args, "action", None)
        
        if action == "add":
            return self.add_user(args)
        elif action == "del":
            return self.delete_user(args)
        elif action == "list":
            return self.list_users(args)
        elif action == "show":
            return self.show_user(args)
        elif action == "regen_password":
            return self.regen_password(args)
        else:
            printer.error(f"Unknown action: {action}")
            sys.exit(1)

    def add_user(self, args):
        username = getattr(args, "username", None)
        if not username:
            printer.error("Username is required. Usage: connpy user --add <username>")
            sys.exit(1)
            
        custom_path = getattr(args, "path", None)
        if custom_path:
            custom_path = custom_path[0] if isinstance(custom_path, list) else custom_path

        try:
            password = getpass.getpass("Enter password for new user: ")
            if not password:
                printer.error("Password cannot be empty.")
                sys.exit(1)
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                printer.error("Passwords do not match.")
                sys.exit(1)
        except (KeyboardInterrupt, EOFError):
            printer.warning("\nOperation cancelled.")
            sys.exit(130)

        try:
            self.app.services.users.create_user(username, password, config_path=custom_path)
            printer.success(f"User '{username}' created successfully.")
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)
        except ValueError as e:
            printer.error(str(e))
            sys.exit(1)
        except Exception as e:
            printer.error(f"Failed to create user: {e}")
            sys.exit(1)

    def delete_user(self, args):
        username = getattr(args, "username", None)
        if not username:
            printer.error("Username is required. Usage: connpy user --del <username>")
            sys.exit(1)

        try:
            self.app.services.users.delete_user(username)
            printer.success(f"User '{username}' deleted successfully.")
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)
        except ValueError as e:
            printer.error(str(e))
            sys.exit(1)
        except Exception as e:
            printer.error(f"Failed to delete user: {e}")
            sys.exit(1)

    def list_users(self, args):
        try:
            users = self.app.services.users.list_users()
            if not users:
                printer.warning("No users registered.")
                return
            
            # Format custom config path, falling back to computed default path instead of null/None
            formatted_users = []
            for u in users:
                formatted_u = u.copy()
                if not formatted_u.get("config_path"):
                    formatted_u["config_path"] = os.path.join(self.app.services.users.users_dir, formatted_u["username"])
                formatted_users.append(formatted_u)
            
            yaml_str = yaml.dump(formatted_users, sort_keys=False, default_flow_style=False)
            printer.data("Registered Users", yaml_str)
        except Exception as e:
            printer.error(f"Failed to list users: {e}")
            sys.exit(1)

    def show_user(self, args):
        username = getattr(args, "username", None)
        if not username:
            printer.error("Username is required. Usage: connpy user --show <username>")
            sys.exit(1)

        try:
            user = self.app.services.users.get_user(username)
            if not user:
                printer.error(f"User '{username}' not found.")
                sys.exit(1)
            
            # Hide the password hash from the CLI output for safety
            safe_user = {k: v for k, v in user.items() if k != "password_hash"}
            if not safe_user.get("config_path"):
                safe_user["config_path"] = os.path.join(self.app.services.users.users_dir, username)
            
            yaml_str = yaml.dump(safe_user, sort_keys=False, default_flow_style=False)
            printer.data(f"User: {username}", yaml_str)
        except ValueError as e:
            printer.error(str(e))
            sys.exit(1)
        except Exception as e:
            printer.error(f"Failed to retrieve user details: {e}")
            sys.exit(1)

    def regen_password(self, args):
        username = getattr(args, "username", None)
        if not username:
            printer.error("Username is required. Usage: connpy user --regen-password <username>")
            sys.exit(1)

        try:
            user = self.app.services.users.get_user(username)
            if not user:
                printer.error(f"User '{username}' not found.")
                sys.exit(1)
        except ValueError as e:
            printer.error(str(e))
            sys.exit(1)
        except Exception as e:
            printer.error(f"Failed to retrieve user details: {e}")
            sys.exit(1)

        try:
            new_password = getpass.getpass("Enter new password: ")
            if not new_password:
                printer.error("Password cannot be empty.")
                sys.exit(1)
            confirm = getpass.getpass("Confirm new password: ")
            if new_password != confirm:
                printer.error("Passwords do not match.")
                sys.exit(1)
        except (KeyboardInterrupt, EOFError):
            printer.warning("\nOperation cancelled.")
            sys.exit(130)

        try:
            self.app.services.users.admin_change_password(username, new_password)
            printer.success(f"Password for user '{username}' regenerated successfully.")
        except ValueError as e:
            printer.error(str(e))
            sys.exit(1)
        except Exception as e:
            printer.error(f"Failed to regenerate password: {e}")
            sys.exit(1)
