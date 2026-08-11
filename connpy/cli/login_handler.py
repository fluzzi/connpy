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
        # Handle token management actions first
        if getattr(args, "create_token", None):
            return self.create_token(args)
        if getattr(args, "list_tokens", False):
            return self.list_tokens(args)
        if getattr(args, "revoke_token", None):
            return self.revoke_token(args)

        if getattr(args, "status", False):
            return self.show_status()

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

    def show_status(self):
        import base64
        import json
        import datetime
        
        token_path = os.path.join(self.app.config.defaultdir, ".token")
        if not os.path.exists(token_path):
            printer.warning("No active session found. You can log in using 'connpy login'.")
            return
            
        try:
            with open(token_path, "r") as f:
                token = f.read().strip()
                
            parts = token.split(".")
            if len(parts) != 3:
                printer.error("Invalid local session token format.")
                return
                
            payload_b64 = parts[1]
            payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            payload = json.loads(payload_bytes.decode("utf-8"))
            
            username = payload.get("sub")
            exp = payload.get("exp")
            
            if not exp:
                printer.success(f"Active session as '{username}' (Indefinite expiration).")
                return
                
            now = datetime.datetime.now(datetime.timezone.utc).timestamp()
            if now > exp:
                printer.error("Session has expired. Please log in again using 'connpy login'.")
                return
                
            remaining = exp - now
            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            
            printer.success(f"Logged in as '{username}'")
            printer.info(f"Time remaining: {hours}h {minutes}m")
            
            exp_dt = datetime.datetime.fromtimestamp(exp, datetime.timezone.utc)
            printer.info(f"Expires at: {exp_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        except Exception as e:
            printer.error(f"Failed to check local session status: {e}")

    def _get_auth_service(self):
        """Gets an authenticated auth service stub, reusing existing or creating one."""
        auth_service = getattr(self.app.services, "auth", None)
        if not auth_service:
            import grpc
            from ..grpc_layer.stubs import AuthStub
            remote_host = self.app.services.remote_host or self.app.config.config.get("remote_host")
            if not remote_host:
                printer.error("Remote host is not configured. Run 'connpy config --remote HOST:PORT' first.")
                sys.exit(1)
            try:
                # Load existing session token for authentication
                token_path = os.path.join(self.app.config.defaultdir, ".token")
                if not os.path.exists(token_path):
                    printer.error("No active session. Please log in first using 'connpy login'.")
                    sys.exit(1)
                with open(token_path, "r") as f:
                    session_token = f.read().strip()
                
                from ..grpc_layer.stubs import AuthClientInterceptor
                interceptor = AuthClientInterceptor(lambda: session_token)
                channel = grpc.intercept_channel(grpc.insecure_channel(remote_host), interceptor)
                auth_service = AuthStub(channel, remote_host=remote_host)
            except Exception as e:
                printer.error(f"Failed to connect to remote server: {e}")
                sys.exit(1)
        return auth_service

    def create_token(self, args):
        auth_service = self._get_auth_service()
        name = args.create_token
        expires_days = getattr(args, "expires_days", 0) or 0

        try:
            result = auth_service.create_api_token(name, expires_in_days=expires_days)
            printer.success(f"API token '{name}' created successfully.")
            printer.warning("⚠  Copy this token now. It will NOT be shown again:")
            printer.data("Token", result["raw_token"])
            printer.info(f"Token ID: {result['token_id']}")
            if expires_days > 0:
                printer.info(f"Expires in: {expires_days} days")
            else:
                printer.info("Expires: Never (permanent)")
        except ConnpyError as e:
            printer.error(f"Failed to create token: {e}")
            sys.exit(1)
        except Exception as e:
            printer.error(f"Failed to create token: {e}")
            sys.exit(1)

    def list_tokens(self, args):
        auth_service = self._get_auth_service()

        try:
            tokens = auth_service.list_api_tokens()
            if not tokens:
                printer.info("No API tokens found.")
                return

            import yaml
            # Clean up empty strings from protobuf defaults
            cleaned = []
            for t in tokens:
                cleaned.append({
                    "token_id": t["token_id"],
                    "name": t["name"],
                    "prefix": t["token_prefix"],
                    "created": t["created_at"] or "N/A",
                    "last_used": t["last_used_at"] or "Never",
                    "expires": t["expires_at"] or "Never",
                })
            yaml_str = yaml.dump(cleaned, sort_keys=False, default_flow_style=False)
            printer.data("API Tokens", yaml_str)
        except ConnpyError as e:
            printer.error(f"Failed to list tokens: {e}")
            sys.exit(1)
        except Exception as e:
            printer.error(f"Failed to list tokens: {e}")
            sys.exit(1)

    def revoke_token(self, args):
        auth_service = self._get_auth_service()
        token_id = args.revoke_token

        try:
            auth_service.revoke_api_token(token_id)
            printer.success(f"Token '{token_id}' revoked successfully.")
        except ConnpyError as e:
            printer.error(f"Failed to revoke token: {e}")
            sys.exit(1)
        except Exception as e:
            printer.error(f"Failed to revoke token: {e}")
            sys.exit(1)
