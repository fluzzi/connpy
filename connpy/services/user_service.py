import os
import hashlib
import re
import shutil
import secrets
import datetime
import bcrypt
import jwt
import yaml
from pathlib import Path
from connpy.configfile import configfile

class UserService:
    def __init__(self, config_dir):
        self.config_dir = os.path.abspath(config_dir)
        self.users_dir = os.path.join(self.config_dir, "users")
        self.registry_file = os.path.join(self.users_dir, "registry.yaml")
        
        # Ensure users directory exists
        os.makedirs(self.users_dir, exist_ok=True)
        
        # Reverse index cache: token_hash -> (username, token_id)
        self._token_index: dict[str, tuple[str, str]] = {}

    def _load_registry(self) -> dict:
        """Loads registry from file. If it doesn't exist, initializes it with a new JWT secret."""
        if not os.path.exists(self.registry_file):
            registry = {
                "jwt_secret": secrets.token_hex(32),
                "users": {}
            }
            self._save_registry(registry)
            return registry
        
        try:
            with open(self.registry_file, "r") as f:
                registry = yaml.safe_load(f) or {}
        except Exception:
            registry = {}
            
        if not isinstance(registry, dict):
            registry = {}
            
        if "jwt_secret" not in registry:
            registry["jwt_secret"] = secrets.token_hex(32)
            
        if "users" not in registry or not isinstance(registry["users"], dict):
            registry["users"] = {}
            
        return registry

    def _save_registry(self, data: dict):
        """Safely saves registry structure to registry.yaml."""
        tmp_file = self.registry_file + ".tmp"
        try:
            with open(tmp_file, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            os.replace(tmp_file, self.registry_file)
            os.chmod(self.registry_file, 0o600)
        except Exception as e:
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass
            raise e

    def _build_token_index(self, registry: dict) -> dict[str, tuple[str, str]]:
        """Builds a reverse index of token_hash -> (username, token_id) for O(1) PAT lookup."""
        index = {}
        for username, user_data in registry.get("users", {}).items():
            for token_id, token_meta in user_data.get("api_tokens", {}).items():
                token_hash = token_meta.get("token_hash")
                if token_hash:
                    index[token_hash] = (username, token_id)
        return index

    def create_user(self, username, password, config_path=None) -> dict:
        """Creates a new user with bcrypt-hashed credentials.
        
        Mode A: config_path=None (fresh user) -> Generates config.yaml and .osk key.
        Mode B: config_path set -> Reuses existing directory after validating its structure.
        """
        if not username or not isinstance(username, str):
            raise ValueError("Username cannot be empty")
            
        if not re.match(r"^[a-zA-Z0-9_-]+$", username):
            raise ValueError("Username must contain only alphanumeric characters, dashes, or underscores")
            
        if not password or not isinstance(password, str):
            raise ValueError("Password cannot be empty")
            
        registry = self._load_registry()
        if username in registry["users"]:
            raise ValueError(f"User '{username}' already exists")
            
        # Resolve path and initialize configuration
        if config_path is None:
            user_dir = os.path.join(self.users_dir, username)
            os.makedirs(user_dir, exist_ok=True)
            
            # Create subdirs for plugins and sessions
            os.makedirs(os.path.join(user_dir, "plugins"), exist_ok=True)
            os.makedirs(os.path.join(user_dir, "ai_sessions"), exist_ok=True)
            
            # Create default config.yaml & .osk key via configfile
            conf_file = os.path.join(user_dir, "config.yaml")
            configfile(conf=conf_file)
            
            stored_config_path = None
        else:
            abs_config_path = os.path.abspath(config_path)
            os.makedirs(abs_config_path, exist_ok=True)
            
            # Create subdirs for plugins and sessions in the custom path
            os.makedirs(os.path.join(abs_config_path, "plugins"), exist_ok=True)
            os.makedirs(os.path.join(abs_config_path, "ai_sessions"), exist_ok=True)
            
            # Create default config.yaml & .osk key via configfile if config.yaml is not present
            conf_file = os.path.join(abs_config_path, "config.yaml")
            if not os.path.exists(conf_file):
                configfile(conf=conf_file)
                
            stored_config_path = abs_config_path

        # Hash password securely
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        
        user_entry = {
            "password_hash": password_hash,
            "config_path": stored_config_path,
            "created": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        
        registry["users"][username] = user_entry
        self._save_registry(registry)
        
        return {
            "username": username,
            "config_path": stored_config_path,
            "created": user_entry["created"]
        }

    def delete_user(self, username):
        """Removes user from the registry and cleans up config directory if server-managed."""
        registry = self._load_registry()
        if username not in registry["users"]:
            raise ValueError(f"User '{username}' not found")
            
        user_data = registry["users"][username]
        config_path = user_data.get("config_path")
        
        if config_path is None:
            user_dir = os.path.join(self.users_dir, username)
            if os.path.exists(user_dir):
                shutil.rmtree(user_dir, ignore_errors=True)
                
        del registry["users"][username]
        self._save_registry(registry)

    def list_users(self) -> list[dict]:
        """Lists all registered users with metadata."""
        registry = self._load_registry()
        return [
            {
                "username": name,
                "config_path": data.get("config_path"),
                "created": data.get("created")
            }
            for name, data in registry.get("users", {}).items()
        ]

    def get_user(self, username) -> dict:
        """Retrieves raw metadata for a specific user."""
        registry = self._load_registry()
        if username not in registry["users"]:
            raise ValueError(f"User '{username}' not found")
            
        data = registry["users"][username]
        return {
            "username": username,
            "config_path": data.get("config_path"),
            "created": data.get("created"),
            "password_hash": data.get("password_hash")
        }

    def change_password(self, username, old_password, new_password):
        """Verifies old password and updates registry with new hashed password."""
        if not new_password or not isinstance(new_password, str):
            raise ValueError("New password cannot be empty")
            
        registry = self._load_registry()
        if username not in registry["users"]:
            raise ValueError(f"User '{username}' not found")
            
        user_data = registry["users"][username]
        if not bcrypt.checkpw(old_password.encode("utf-8"), user_data["password_hash"].encode("utf-8")):
            raise ValueError("Invalid credentials")
            
        # Update hash
        user_data["password_hash"] = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        self._save_registry(registry)

    def admin_change_password(self, username, new_password):
        """Administrative password override (does not require old password)."""
        if not new_password or not isinstance(new_password, str):
            raise ValueError("New password cannot be empty")
            
        registry = self._load_registry()
        if username not in registry["users"]:
            raise ValueError(f"User '{username}' not found")
            
        user_data = registry["users"][username]
        user_data["password_hash"] = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        self._save_registry(registry)

    def authenticate(self, username, password) -> bool:
        """Verifies if the credentials are valid using bcrypt."""
        registry = self._load_registry()
        if username not in registry["users"]:
            return False
            
        user_data = registry["users"][username]
        return bcrypt.checkpw(password.encode("utf-8"), user_data["password_hash"].encode("utf-8"))

    def generate_jwt(self, username) -> str:
        """Generates a secure JSON Web Token for the user expiring in 12 hours."""
        registry = self._load_registry()
        if username not in registry["users"]:
            raise ValueError(f"User '{username}' not found")
            
        expiration = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=12)
        payload = {
            "sub": username,
            "exp": expiration
        }
        
        secret = os.environ.get("CONNPY_JWT_SECRET") or registry["jwt_secret"]
        token = jwt.encode(payload, secret, algorithm="HS256")
        if isinstance(token, bytes):
            token = token.decode("utf-8")
            
        return token

    def verify_jwt(self, token) -> str | None:
        """Decodes JWT and returns username if token is valid and unexpired."""
        registry = self._load_registry()
        try:
            secret = os.environ.get("CONNPY_JWT_SECRET") or registry["jwt_secret"]
            payload = jwt.decode(token, secret, algorithms=["HS256"])
            return payload.get("sub")
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, KeyError):
            return None

    # --- Personal Access Token (PAT) Management ---

    def create_api_token(self, username: str, name: str, expires_in_days: int | None = None) -> dict:
        """Creates a Personal Access Token for the user.
        
        Returns the raw token ONCE. Only the SHA-256 hash is persisted.
        """
        if not name or not isinstance(name, str):
            raise ValueError("Token name cannot be empty")
        
        registry = self._load_registry()
        if username not in registry["users"]:
            raise ValueError(f"User '{username}' not found")
        
        user_data = registry["users"][username]
        if "api_tokens" not in user_data:
            user_data["api_tokens"] = {}
        
        # Generate cryptographically secure token with recognizable prefix
        raw_secret = secrets.token_hex(32)
        raw_token = f"cnp_pat_{raw_secret}"
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        token_id = f"tok_{secrets.token_hex(4)}"
        
        now = datetime.datetime.now(datetime.timezone.utc)
        expires_at = None
        if expires_in_days and expires_in_days > 0:
            expires_at = (now + datetime.timedelta(days=expires_in_days)).isoformat()
        
        user_data["api_tokens"][token_id] = {
            "name": name,
            "token_hash": token_hash,
            "token_prefix": raw_token[:16],
            "created_at": now.isoformat(),
            "last_used_at": None,
            "expires_at": expires_at,
        }
        
        self._save_registry(registry)
        self._token_index = self._build_token_index(registry)
        
        return {
            "token_id": token_id,
            "raw_token": raw_token,
            "name": name,
        }

    def list_api_tokens(self, username: str) -> list[dict]:
        """Lists all active API tokens for a user (without sensitive data)."""
        registry = self._load_registry()
        if username not in registry["users"]:
            raise ValueError(f"User '{username}' not found")
        
        tokens = registry["users"][username].get("api_tokens", {})
        return [
            {
                "token_id": tid,
                "name": meta.get("name"),
                "token_prefix": meta.get("token_prefix"),
                "created_at": meta.get("created_at"),
                "last_used_at": meta.get("last_used_at"),
                "expires_at": meta.get("expires_at"),
            }
            for tid, meta in tokens.items()
        ]

    def revoke_api_token(self, username: str, token_id: str) -> bool:
        """Revokes (deletes) a specific API token. Returns True if found and removed."""
        registry = self._load_registry()
        if username not in registry["users"]:
            raise ValueError(f"User '{username}' not found")
        
        tokens = registry["users"][username].get("api_tokens", {})
        if token_id not in tokens:
            return False
        
        del tokens[token_id]
        self._save_registry(registry)
        self._token_index = self._build_token_index(registry)
        return True

    def verify_api_token(self, raw_token: str) -> str | None:
        """Validates a PAT by hashing it and looking up the reverse index.
        
        Returns username if valid and not expired, None otherwise.
        """
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        
        # Rebuild index if empty (cold start or after process restart)
        if not self._token_index:
            registry = self._load_registry()
            self._token_index = self._build_token_index(registry)
        
        match = self._token_index.get(token_hash)
        if not match:
            return None
        
        username, token_id = match
        
        # Validate token still exists and check expiration
        registry = self._load_registry()
        user_data = registry.get("users", {}).get(username, {})
        token_meta = user_data.get("api_tokens", {}).get(token_id)
        
        if not token_meta:
            # Token was revoked between index build and now
            self._token_index = self._build_token_index(registry)
            return None
        
        # Check expiration
        expires_at = token_meta.get("expires_at")
        if expires_at:
            exp_dt = datetime.datetime.fromisoformat(expires_at)
            if datetime.datetime.now(datetime.timezone.utc) > exp_dt:
                return None
        
        # Update last_used_at
        token_meta["last_used_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._save_registry(registry)
        
        return username
