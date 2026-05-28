import os
import threading
from connpy.configfile import configfile
from connpy.services.provider import ServiceProvider
from connpy.services.user_service import UserService

class UserRegistry:
    """Holds per-user ServiceProviders in memory, thread-safe with hot-reloading."""
    def __init__(self, server_config_dir):
        self.server_config_dir = os.path.abspath(server_config_dir)
        self.user_service = UserService(self.server_config_dir)
        self._providers = {}   # username → ServiceProvider
        self._mtimes = {}      # username → last loaded mtime (float)
        self._lock = threading.Lock()
        
        # Load shared/global config
        self._shared_conf_file = os.path.join(self.server_config_dir, "config.yaml")
        if os.path.exists(self._shared_conf_file):
            self._shared_config = configfile(conf=self._shared_conf_file)
            self._shared_mtime = os.path.getmtime(self._shared_conf_file)
        else:
            self._shared_config = None
            self._shared_mtime = 0.0

    def _refresh_shared(self):
        """Hot-reload shared config if the file changed on disk."""
        if not os.path.exists(self._shared_conf_file):
            return
        current_mtime = os.path.getmtime(self._shared_conf_file)
        if current_mtime > self._shared_mtime:
            try:
                self._shared_config = configfile(conf=self._shared_conf_file)
                self._shared_mtime = current_mtime
                # Clear all user providers so they pick up the new shared config
                self._providers.clear()
                self._mtimes.clear()
            except Exception as e:
                from connpy import printer
                printer.warning(f"Failed to reload shared config: {e}")
    
    def get_provider(self, username) -> ServiceProvider:
        """Get, lazy-load, or hot-reload a user's full ServiceProvider."""
        with self._lock:
            # Refresh shared/global config if it has changed
            self._refresh_shared()
            
            # 1. Resolve physical path of the user's config.yaml file
            user_data = self.user_service.get_user(username)
            config_path = user_data.get("config_path")
            if config_path:
                conf_file = os.path.join(config_path, "config.yaml")
            else:
                conf_file = os.path.join(self.server_config_dir, "users", username, "config.yaml")
            
            # 2. Retrieve actual modification time in disk
            current_mtime = os.path.getmtime(conf_file) if os.path.exists(conf_file) else 0.0
            
            # 3. Validate if initial load or hot-reload is required
            if username not in self._providers or self._mtimes.get(username, 0.0) < current_mtime:
                old_provider = self._providers.get(username)
                
                try:
                    # Attempt a fresh configuration load
                    config = configfile(conf=conf_file, shared_config=self._shared_config)
                    new_provider = ServiceProvider(config, mode="local")
                    
                    # Successfully loaded, clean up the old provider
                    if old_provider:
                        self._providers.pop(username, None)
                        if hasattr(old_provider, "close"):
                            try:
                                old_provider.close()
                            except Exception:
                                pass
                                
                    self._providers[username] = new_provider
                    self._mtimes[username] = current_mtime
                    
                except Exception as e:
                    # Log warning but fallback to the old stable provider in memory if available
                    from connpy import printer
                    printer.warning(f"Failed to hot-reload config for user '{username}' (file may be corrupt/incomplete): {e}")
                    if old_provider:
                        # Keep serving with the old cached instance to ensure service continuity
                        self._mtimes[username] = current_mtime
                    else:
                        # No fallback exists, propagate the exception
                        raise e
                    
            return self._providers[username]
    
    def has_users(self) -> bool:
        """Check if any users are registered (enables auth enforcement)."""
        return bool(self.user_service.list_users())
    
    def evict(self, username):
        """Remove and cleanly shut down cached provider (after delete or password change)."""
        with self._lock:
            provider = self._providers.pop(username, None)
            self._mtimes.pop(username, None)
            if provider:
                # Explicit cleanup of user-scoped resources if custom close/cleanup exists
                if hasattr(provider, "close"):
                    try:
                        provider.close()
                    except Exception:
                        pass
