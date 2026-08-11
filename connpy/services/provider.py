from .exceptions import InvalidConfigurationError

class RemoteStub:
    def __getattr__(self, name):
        raise NotImplementedError(
            "Remote mode (gRPC) is not yet available. "
            "Use local mode or wait for the gRPC implementation."
        )

class ServiceProvider:
    """Dynamic service backend. Transparently provides local or remote services."""
    
    def __init__(self, config, mode="local", remote_host=None):
        self.mode = mode
        self.config = config
        self.remote_host = remote_host
        self._system = None
        self._execution = None
        self._import_export = None
        self._sync = None
        self._users = None
        self._ai = None
        
        if mode == "local":
            self._init_local()
        elif mode == "remote":
            self._init_remote()
        else:
            raise ValueError(f"Unknown service mode: {mode}")
    
    def _init_local(self):
        from .node_service import NodeService
        from .profile_service import ProfileService
        from .config_service import ConfigService
        from .plugin_service import PluginService
        from .context_service import ContextService
        
        self.nodes = NodeService(self.config)
        self.profiles = ProfileService(self.config)
        self.config_svc = ConfigService(self.config)
        self.plugins = PluginService(self.config)
        self.context = ContextService(self.config)
    
    def _init_remote(self):
        # Allow ConfigService to work locally so the user can revert the mode
        from .config_service import ConfigService
        from .context_service import ContextService
        self.config_svc = ConfigService(self.config)
        self.context = ContextService(self.config)
        
        if not self.remote_host:
            raise InvalidConfigurationError("Remote host must be specified in remote mode")

        import grpc
        import os
        from ..grpc_layer.stubs import (
            NodeStub, ProfileStub, PluginStub, AIStub, 
            ExecutionStub, ImportExportStub, SystemStub,
            ConfigStub, AuthClientInterceptor, AuthStub
        )
        
        def get_token():
            env_token = os.environ.get("CONNPY_TOKEN")
            if env_token:
                return env_token
            token_path = os.path.join(self.config.defaultdir, ".token")
            if os.path.exists(token_path):
                try:
                    with open(token_path, "r") as f:
                        return f.read().strip()
                except Exception:
                    pass
            return None

        channel = grpc.insecure_channel(self.remote_host)
        interceptor = AuthClientInterceptor(get_token)
        channel = grpc.intercept_channel(channel, interceptor)
        
        # Surgical fix: Keep ConfigService local for mode/theme management, 
        # but delegate encryption to the server stub.
        config_remote = ConfigStub(channel, remote_host=self.remote_host)
        self.config_svc.encrypt_password = config_remote.encrypt_password

        self.nodes = NodeStub(channel, remote_host=self.remote_host, config=self.config)
        self.profiles = ProfileStub(channel, remote_host=self.remote_host, node_stub=self.nodes)
        self.plugins = PluginStub(channel, remote_host=self.remote_host)
        self.ai = AIStub(channel, remote_host=self.remote_host)
        self.system = SystemStub(channel, remote_host=self.remote_host)
        self.execution = ExecutionStub(channel, remote_host=self.remote_host)
        self.import_export = ImportExportStub(channel, remote_host=self.remote_host)
        self.auth = AuthStub(channel, remote_host=self.remote_host)

    @property
    def system(self):
        if self._system is None and self.mode == "local":
            from .system_service import SystemService
            self._system = SystemService(self.config)
        return self._system

    @system.setter
    def system(self, value):
        self._system = value

    @property
    def execution(self):
        if self._execution is None and self.mode == "local":
            from .execution_service import ExecutionService
            self._execution = ExecutionService(self.config)
        return self._execution

    @execution.setter
    def execution(self, value):
        self._execution = value

    @property
    def import_export(self):
        if self._import_export is None and self.mode == "local":
            from .import_export_service import ImportExportService
            self._import_export = ImportExportService(self.config)
        return self._import_export

    @import_export.setter
    def import_export(self, value):
        self._import_export = value

    @property
    def sync(self):
        if self._sync is None:
            from .sync_service import SyncService
            self._sync = SyncService(self.config)
        return self._sync

    @sync.setter
    def sync(self, value):
        self._sync = value

    @property
    def users(self):
        if self._users is None and self.mode == "local":
            from .user_service import UserService
            self._users = UserService(self.config.defaultdir)
        return self._users

    @users.setter
    def users(self, value):
        self._users = value

    @property
    def ai(self):
        if self._ai is None and self.mode == "local":
            from .ai_service import AIService
            self._ai = AIService(self.config)
        return self._ai

    @ai.setter
    def ai(self, value):
        self._ai = value
