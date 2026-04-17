import sys
import yaml
from .. import printer

class SyncHandler:
    def __init__(self, app):
        self.app = app

    def dispatch(self, args):
        action = getattr(args, "action", None)
        actions = {
            "login": self.login,
            "logout": self.logout,
            "status": self.status,
            "list": self.list_backups,
            "once": self.once,
            "restore": self.restore,
            "start": self.start,
            "stop": self.stop
        }
        handler = actions.get(action)
        if handler:
            return handler(args)
        
        return self.status(args)

    def login(self, args):
        self.app.services.sync.login()

    def logout(self, args):
        self.app.services.sync.logout()

    def status(self, args):
        status = self.app.services.sync.check_login_status()
        enabled = self.app.services.sync.sync_enabled
        remote = self.app.services.sync.sync_remote
        
        printer.info(f"Login Status: {status}")
        printer.info(f"Auto-Sync: {'Enabled' if enabled else 'Disabled'}")
        printer.info(f"Sync Remote Nodes: {'Yes' if remote else 'No'}")

    def list_backups(self, args):
        backups = self.app.services.sync.list_backups()
        if backups:
            yaml_output = yaml.dump(backups, sort_keys=False, default_flow_style=False)
            printer.custom("backups", "")
            print(yaml_output)
        else:
            printer.info("No backups found or not logged in.")

    def once(self, args):
        # Manual backup. We check if we should include remote nodes
        remote_data = None
        if self.app.services.sync.sync_remote and self.app.services.mode == "remote":
            inventory = self.app.services.nodes.get_inventory()
            # Merge with local settings
            local_settings = self.app.services.config_svc.get_settings()
            local_settings.pop("configfolder", None)

            # Maintain proper config structure: {config: {}, connections: {}, profiles: {}}
            remote_data = {
                "config": local_settings,
                "connections": inventory.get("connections", {}),
                "profiles": inventory.get("profiles", {})
            }
            
        if self.app.services.sync.compress_and_upload(remote_data):
            printer.success("Manual backup completed.")

    def restore(self, args):
        import inquirer
        file_id = getattr(args, "id", None)
        
        # Segmented flags
        restore_config = getattr(args, "restore_config", False)
        restore_nodes = getattr(args, "restore_nodes", False)
        
        # If neither is specified, we restore ALL (backwards compatibility)
        if not restore_config and not restore_nodes:
            restore_config = True
            restore_nodes = True
            
        # 1. Analyze what we are about to restore
        info = self.app.services.sync.analyze_backup_content(file_id)
        if not info:
            printer.error("Could not analyze backup content.")
            return

        # 2. Show detailed info
        printer.info("Restoration Details:")
        if restore_config:
            print(f"    - Local Settings: Yes")
            print(f"    - RSA Key (.osk): {'Yes' if info['has_key'] else 'No'}")
        if restore_nodes:
            target = "REMOTE" if self.app.services.mode == "remote" else "LOCAL"
            print(f"    - Nodes: {info['nodes']}")
            print(f"    - Folders: {info['folders']}")
            print(f"    - Profiles: {info['profiles']}")
            print(f"    - Destination: {target}")
        print("")

        questions = [inquirer.Confirm("confirm", message="Do you want to proceed with the restoration?", default=False)]
        answers = inquirer.prompt(questions)
        
        if not answers or not answers["confirm"]:
            printer.info("Restore cancelled.")
            return

        # 3. Perform the actual restore
        if self.app.services.sync.restore_backup(
            file_id=file_id, 
            restore_config=restore_config, 
            restore_nodes=restore_nodes,
            app_instance=self.app
        ):
            printer.success("Restore completed successfully.")

    def start(self, args):
        self.app.services.config_svc.update_setting("sync", True)
        self.app.services.sync.sync_enabled = True
        printer.success("Auto-sync enabled.")

    def stop(self, args):
        self.app.services.config_svc.update_setting("sync", False)
        self.app.services.sync.sync_enabled = False
        printer.success("Auto-sync disabled.")
