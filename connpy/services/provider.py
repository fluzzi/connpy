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
        from .ai_service import AIService
        from .system_service import SystemService
        from .execution_service import ExecutionService
        from .import_export_service import ImportExportService
        from .context_service import ContextService
        from .sync_service import SyncService
        
        self.nodes = NodeService(self.config)
        self.profiles = ProfileService(self.config)
        self.config_svc = ConfigService(self.config)
        self.plugins = PluginService(self.config)
        self.ai = AIService(self.config)
        self.system = SystemService(self.config)
        self.execution = ExecutionService(self.config)
        self.import_export = ImportExportService(self.config)
        self.context = ContextService(self.config)
        self.sync = SyncService(self.config)
    
    def _init_remote(self):
        # Allow ConfigService to work locally so the user can revert the mode
        from .config_service import ConfigService
        from .context_service import ContextService
        from .sync_service import SyncService
        self.config_svc = ConfigService(self.config)
        self.context = ContextService(self.config)
        self.sync = SyncService(self.config)
        
        if not self.remote_host:
            raise InvalidConfigurationError("Remote host must be specified in remote mode")

        import grpc
        from ..grpc.stubs import NodeStub, ProfileStub, PluginStub, AIStub, ExecutionStub, ImportExportStub, SystemStub
        
        channel = grpc.insecure_channel(self.remote_host)
        
        self.nodes = NodeStub(channel, remote_host=self.remote_host, config=self.config)
        self.profiles = ProfileStub(channel, remote_host=self.remote_host, node_stub=self.nodes)
        self.plugins = PluginStub(channel, remote_host=self.remote_host)
        self.ai = AIStub(channel, remote_host=self.remote_host)
        self.system = SystemStub(channel, remote_host=self.remote_host)
        self.execution = ExecutionStub(channel, remote_host=self.remote_host)
        self.import_export = ImportExportStub(channel, remote_host=self.remote_host)
