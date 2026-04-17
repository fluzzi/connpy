class ConnpyError(Exception):
    """Base exception for all connpy services."""
    pass

class NodeNotFoundError(ConnpyError):
    """Raised when a connection or folder is not found."""
    pass

class NodeAlreadyExistsError(ConnpyError):
    """Raised when a node or folder already exists."""
    pass

class ProfileNotFoundError(ConnpyError):
    """Raised when a profile is not found."""
    pass

class ProfileAlreadyExistsError(ConnpyError):
    """Raised when a profile with the same name already exists."""
    pass

class ExecutionError(ConnpyError):
    """Raised when an execution fails or returns error."""
    pass

class InvalidConfigurationError(ConnpyError):
    """Raised when data or configuration input is invalid."""
    pass

class ReservedNameError(ConnpyError):
    """Raised when a node name conflicts with a reserved command."""
    pass
