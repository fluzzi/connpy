from .exceptions import *
from .node_service import NodeService
from .profile_service import ProfileService
from .plugin_service import PluginService
from .config_service import ConfigService

def __getattr__(name: str):
    if name == "ExecutionService":
        from .execution_service import ExecutionService
        globals()["ExecutionService"] = ExecutionService
        return ExecutionService
    elif name == "ImportExportService":
        from .import_export_service import ImportExportService
        globals()["ImportExportService"] = ImportExportService
        return ImportExportService
    elif name == "SystemService":
        from .system_service import SystemService
        globals()["SystemService"] = SystemService
        return SystemService
    elif name == "SyncService":
        from .sync_service import SyncService
        globals()["SyncService"] = SyncService
        return SyncService
    elif name == "UserService":
        from .user_service import UserService
        globals()["UserService"] = UserService
        return UserService
    elif name == "AIService":
        from .ai_service import AIService
        globals()["AIService"] = AIService
        return AIService
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

__all__ = [
    'NodeService',
    'ProfileService',
    'ExecutionService',
    'ImportExportService',
    'AIService',
    'PluginService',
    'ConfigService',
    'SystemService',
    'SyncService',
    'UserService',
    'ConnpyError',
    'NodeNotFoundError',
    'NodeAlreadyExistsError',
    'ProfileNotFoundError',
    'ProfileAlreadyExistsError',
    'ExecutionError',
    'InvalidConfigurationError'
]

