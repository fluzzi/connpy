import os
import sys
import getpass
from .. import printer
from ..services.exceptions import ConnpyError

class LoginHandler:
    def __init__(self, app):
        self.app = app

    def dispatch(self, args):
        action = getattr(args, "action", None)
        if action == "login":
            return self.login(args)
        elif action == "logout":
            return self.logout(args)
        else:
            printer.error(f"Unknown action: {action}")
            sys.exit(1)

    def login(self, args):
        if self.app.services.mode != "remote":
            printer.warning("Note: Your current configuration is set to local mode. Logging in will save credentials, but they will only apply when service-mode is set to 'remote'.")

        username = getattr(args, "username", None)
        if not username:
            try:
                username = input("Username: ").strip()
                if not username:
                    printer.error("Username cannot be empty.")
                    sys.exit(1)
            except (KeyboardInterrupt, EOFError):
                printer.warning("\nOperation cancelled.")
                sys.exit(130)

        try:
            password = getpass.getpass("Password: ")
            if not password:
                printer.error("Password cannot be empty.")
                sys.exit(1)
        except (KeyboardInterrupt, EOFError):
            printer.warning("\nOperation cancelled.")
            sys.exit(130)

        # Make the gRPC login call via self.app.services.auth stub
        # We need to make sure auth is initialized in remote mode.
        # If we are in local mode, self.app.services.auth is not initialized on ServiceProvider.
        # Let's instantiate it dynamically if it's not present.
        auth_service = getattr(self.app.services, "auth", None)
        if not auth_service:
            import grpc
            from ..grpc_layer.stubs import AuthStub
            remote_host = self.app.services.remote_host or self.app.config.config.get("remote_host")
            if not remote_host:
                printer.error("Remote host is not configured. Run 'connpy config --remote HOST:PORT' first.")
                sys.exit(1)
            try:
                channel = grpc.insecure_channel(remote_host)
                auth_service = AuthStub(channel, remote_host=remote_host)
            except Exception as e:
                printer.error(f"Failed to connect to remote server for login: {e}")
                sys.exit(1)

        try:
            res = auth_service.login(username, password)
            token = res["token"]
            
            # Save token to ~/.config/conn/.token
            token_path = os.path.join(self.app.config.defaultdir, ".token")
            with open(token_path, "w") as f:
                f.write(token)
            os.chmod(token_path, 0o600)
            
            printer.success(f"Logged in successfully as '{username}'. Session expires in 8 hours.")
        except ConnpyError as e:
            printer.error(f"Login failed: {e}")
            sys.exit(1)
        except Exception as e:
            printer.error(f"Login failed with unexpected error: {e}")
            sys.exit(1)

    def logout(self, args):
        token_path = os.path.join(self.app.config.defaultdir, ".token")
        if os.path.exists(token_path):
            try:
                os.remove(token_path)
                printer.success("Logged out successfully. Local session cleared.")
            except Exception as e:
                printer.error(f"Failed to clear session: {e}")
                sys.exit(1)
        else:
            printer.info("No active session found (already logged out).")
