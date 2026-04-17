import re
from .base import BaseService
from .exceptions import (
    NodeNotFoundError, NodeAlreadyExistsError, 
    InvalidConfigurationError, ReservedNameError
)

class NodeService(BaseService):
    def __init__(self, config=None):
        super().__init__(config)


    def list_nodes(self, filter_str=None, format_str=None):
        """Return a listed filtered by regex match and formatted if needed."""
        nodes = self.config._getallnodes()
        case_sensitive = self.config.config.get("case", False)
        
        if filter_str:
            flags = re.IGNORECASE if not case_sensitive else 0
            nodes = [n for n in nodes if re.search(filter_str, n, flags)]
            
        if not format_str:
            return nodes
            
        from .profile_service import ProfileService
        profile_service = ProfileService(self.config)
        
        formatted_nodes = []
        for n_id in nodes:
            # Use ProfileService to resolve profiles for dynamic formatting
            details = self.config.getitem(n_id, extract=False)
            if details:
                details = profile_service.resolve_node_data(details)
                
                name = n_id.split("@")[0]
                location = n_id.partition("@")[2] or "root"
                
                # Prepare context for .format() with all details
                context = details.copy()
                context.update({
                    "name": name,
                    "NAME": name.upper(),
                    "location": location,
                    "LOCATION": location.upper(),
                })
                
                # Add exploded uniques (id, folder, subfolder)
                uniques = self.config._explode_unique(n_id)
                if uniques:
                    context.update(uniques)
                
                # Add uppercase versions of all keys for convenience
                for k, v in list(context.items()):
                    if isinstance(v, str):
                        context[k.upper()] = v.upper()
                
                try:
                    formatted_nodes.append(format_str.format(**context))
                except (KeyError, IndexError, ValueError):
                    # Fallback to original string if format fails
                    formatted_nodes.append(n_id)
        return formatted_nodes

    def list_folders(self, filter_str=None):
        """Return all unique folders, optionally filtered by regex."""
        folders = self.config._getallfolders()
        case_sensitive = self.config.config.get("case", False)
        
        if filter_str:
            flags = re.IGNORECASE if not case_sensitive else 0
            folders = [f for f in folders if re.search(filter_str, f, flags)]
        return folders

    def get_node_details(self, unique_id):
        """Return full configuration dictionary for a specific node."""
        details = self.config.getitem(unique_id)
        if not details:
            raise NodeNotFoundError(f"Node '{unique_id}' not found.")
        return details

    def explode_unique(self, unique_id):
        """Explode a unique ID into a dictionary of its parts."""
        return self.config._explode_unique(unique_id)

    def generate_cache(self, nodes=None, folders=None, profiles=None):
        """Generate and update the internal nodes cache."""
        self.config._generate_nodes_cache(nodes=nodes, folders=folders, profiles=profiles)


    def add_node(self, unique_id, data, is_folder=False):
        """Logic for adding a new node or folder to configuration."""
        if not is_folder:
            self._validate_node_name(unique_id)
            
        all_nodes = self.config._getallnodes()
        all_folders = self.config._getallfolders()
        
        if is_folder:
            if unique_id in all_folders:
                raise NodeAlreadyExistsError(f"Folder '{unique_id}' already exists.")
            uniques = self.config._explode_unique(unique_id)
            if not uniques:
                raise InvalidConfigurationError(f"Invalid folder name '{unique_id}'.")
            
            # Check if parent folder exists when creating a subfolder
            if "subfolder" in uniques:
                parent_folder = f"@{uniques['folder']}"
                if parent_folder not in all_folders:
                    raise NodeNotFoundError(f"Folder '{parent_folder}' not found.")
                    
            self.config._folder_add(**uniques)
            self.config._saveconfig(self.config.file)
        else:
            if unique_id in all_nodes:
                raise NodeAlreadyExistsError(f"Node '{unique_id}' already exists.")
                
            # Check if parent folder exists when creating a node in a folder
            node_folder = unique_id.partition("@")[2]
            if node_folder:
                parent_folder = f"@{node_folder}"
                if parent_folder not in all_folders:
                    raise NodeNotFoundError(f"Folder '{parent_folder}' not found.")
                    
            # Ensure 'id' is in data for config._connections_add
            if "id" not in data:
                uniques = self.config._explode_unique(unique_id)
                if uniques and "id" in uniques:
                    data["id"] = uniques["id"]
            
            self.config._connections_add(**data)
            self.config._saveconfig(self.config.file)

    def update_node(self, unique_id, data):
        """Explicitly update an existing node."""
        all_nodes = self.config._getallnodes()
        if unique_id not in all_nodes:
            raise NodeNotFoundError(f"Node '{unique_id}' not found.")
            
        # Ensure 'id' is in data for config._connections_add
        if "id" not in data:
            uniques = self.config._explode_unique(unique_id)
            if uniques:
                data["id"] = uniques["id"]
            
        # config._connections_add actually handles updates if ID exists correctly
        self.config._connections_add(**data)
        self.config._saveconfig(self.config.file)

    def delete_node(self, unique_id, is_folder=False):
        """Logic for deleting a node or folder."""
        if is_folder:
            uniques = self.config._explode_unique(unique_id)
            if not uniques:
                raise NodeNotFoundError(f"Folder '{unique_id}' not found or invalid.")
            self.config._folder_del(**uniques)
        else:
            uniques = self.config._explode_unique(unique_id)
            if not uniques:
                raise NodeNotFoundError(f"Node '{unique_id}' not found or invalid.")
            self.config._connections_del(**uniques)
            
        self.config._saveconfig(self.config.file)

    def connect_node(self, unique_id, sftp=False, debug=False, logger=None):
        """Interact with a node directly."""
        from connpy.core import node
        from .profile_service import ProfileService
        
        node_data = self.config.getitem(unique_id, extract=False)
        if not node_data:
            raise NodeNotFoundError(f"Node '{unique_id}' not found.")
            
        # Resolve profiles
        profile_service = ProfileService(self.config)
        resolved_data = profile_service.resolve_node_data(node_data)
            
        n = node(unique_id, **resolved_data, config=self.config)
        if sftp:
            n.protocol = "sftp"
            
        n.interact(debug=debug, logger=logger)

    def move_node(self, src_id, dst_id, copy=False):
        """Move or copy a node."""
        self._validate_node_name(dst_id)
        
        node_data = self.config.getitem(src_id)
        if not node_data:
            raise NodeNotFoundError(f"Source node '{src_id}' not found.")
            
        if dst_id in self.config._getallnodes():
            raise NodeAlreadyExistsError(f"Destination node '{dst_id}' already exists.")
            
        new_uniques = self.config._explode_unique(dst_id)
        if not new_uniques:
            raise InvalidConfigurationError(f"Invalid destination format '{dst_id}'.")
            
        new_node_data = node_data.copy()
        new_node_data.update(new_uniques)
        
        self.config._connections_add(**new_node_data)
        
        if not copy:
            src_uniques = self.config._explode_unique(src_id)
            self.config._connections_del(**src_uniques)
            
        self.config._saveconfig(self.config.file)

    def bulk_add(self, ids, hosts, common_data):
        """Add multiple nodes with shared common configuration."""
        count = 0
        all_nodes = self.config._getallnodes()
        
        for i, uid in enumerate(ids):
            if uid in all_nodes:
                continue
                
            try:
                self._validate_node_name(uid)
            except ReservedNameError:
                # For bulk, we might want to just skip or log. 
                # CLI caller will handle if it wants to be strict.
                continue
                
            host = hosts[i] if i < len(hosts) else hosts[0]
            uniques = self.config._explode_unique(uid)
            if not uniques:
                continue
                
            node_data = common_data.copy()
            node_data.pop("ids", None)
            node_data.pop("location", None)
            node_data.update(uniques)
            node_data["host"] = host
            node_data["type"] = "connection"

            self.config._connections_add(**node_data)
            count += 1
            
        if count > 0:
            self.config._saveconfig(self.config.file)
        return count

    def full_replace(self, connections, profiles):
        """Replace all connections and profiles with new data."""
        self.config.connections = connections
        self.config.profiles = profiles
        self.config._saveconfig(self.config.file)

    def get_inventory(self):
        """Return a full snapshot of connections and profiles."""
        return {
            "connections": self.config.connections,
            "profiles": self.config.profiles
        }
