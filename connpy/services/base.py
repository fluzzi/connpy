from connpy.hooks import MethodHook

class BaseService:
    """Base class for all connpy services, providing common configuration access."""
    
    def __init__(self, config=None):
        """
        Initialize the service.
        
        Args:
            config: An instance of configfile (or None to instantiate a new one/use global context).
        """
        from connpy import configfile
        self.config = config or configfile()
        self.hooks = MethodHook
        self.reserved_names = []

    def set_reserved_names(self, names):
        """Inject a list of reserved names (e.g. from the CLI)."""
        self.reserved_names = names

    def _validate_node_name(self, unique_id):
        """Check if the node name in unique_id is reserved."""
        from .exceptions import ReservedNameError
        if not self.reserved_names:
            return
            
        uniques = self.config._explode_unique(unique_id)
        if uniques and "id" in uniques:
            # We only validate the 'id' (the actual node name), folders are prefixed with @
            node_name = uniques["id"]
            if node_name in self.reserved_names:
                raise ReservedNameError(f"Node name '{node_name}' is a reserved command.")
