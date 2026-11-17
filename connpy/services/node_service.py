import inspect
import re
from copy import deepcopy
from typing import ClassVar

from .base import BaseService
from .exceptions import (
    InvalidConfigurationError,
    NodeAlreadyExistsError,
    NodeNotFoundError,
    ReservedNameError,
)


def _supports_transactional_update(cfg):
    return (
        cfg is not None
        and not hasattr(cfg, "assert_called")
        and callable(getattr(cfg, "transactional_update", None))
    )


class NodeService(BaseService):
    _CONNECTION_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "id",
        "host",
        "folder",
        "subfolder",
        "options",
        "logs",
        "password",
        "port",
        "protocol",
        "user",
        "tags",
        "jumphost",
        "fallback",
        "type",
    })

    def __init__(self, config=None):
        super().__init__(config)

    @staticmethod
    def _validate_node_payload(data):
        if not isinstance(data, dict):
            return
        from ..auth_utils import (
            SanitizedAuthError,
            is_compose_expression,
            validate_auth_budget_tags,
            validate_compose_syntax,
            validate_fallback_entries,
        )

        try:
            if "tags" in data and data["tags"] not in ("", None):
                validate_auth_budget_tags(data["tags"])
            if "fallback" in data and data["fallback"] not in (None, "", []):
                validate_fallback_entries(data["fallback"])
            pw = data.get("password")
            if is_compose_expression(pw):
                validate_compose_syntax(pw)
            elif isinstance(pw, (list, tuple)):
                for item in pw:
                    if is_compose_expression(item):
                        validate_compose_syntax(item)
        except (ValueError, TypeError, SanitizedAuthError) as exc:
            raise InvalidConfigurationError(str(exc)) from None


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
            if filter_str.startswith("@"):
                if not case_sensitive:
                    folders = [f for f in folders if f.lower() == filter_str.lower()]
                else:
                    folders = [f for f in folders if f == filter_str]
            else:
                flags = re.IGNORECASE if not case_sensitive else 0
                folders = [f for f in folders if re.search(filter_str, f, flags)]
        return folders

    def get_node_details(self, unique_id):
        """Return full configuration dictionary for a specific node."""
        try:
            details = self.config.getitem(unique_id)
            if not details:
                raise NodeNotFoundError(f"Node '{unique_id}' not found.")
            return details
        except (KeyError, TypeError):
            raise NodeNotFoundError(f"Node '{unique_id}' not found.") from None

    def explode_unique(self, unique_id):
        """Explode a unique ID into a dictionary of its parts."""
        return self.config._explode_unique(unique_id)

    def generate_cache(self, nodes=None, folders=None, profiles=None):
        """Generate and update the internal nodes cache."""
        self.config._generate_nodes_cache(nodes=nodes, folders=folders, profiles=profiles)

    def validate_parent_folder(self, unique_id, is_folder=False):
        """Check if parent folder exists for a given node unique ID."""
        if is_folder:
            uniques = self.config._explode_unique(unique_id)
            if uniques and "subfolder" in uniques and "folder" in uniques:
                parent_folder = f"@{uniques['folder']}"
                if parent_folder not in self.config._getallfolders():
                    raise NodeNotFoundError(f"Folder '{parent_folder}' not found.")
        else:
            node_folder = unique_id.partition("@")[2]
            if node_folder:
                parent_folder = f"@{node_folder}"
                if parent_folder not in self.config._getallfolders():
                    raise NodeNotFoundError(f"Folder '{parent_folder}' not found.")


    def add_node(self, unique_id, data, is_folder=False):
        """Logic for adding a new node or folder to configuration."""
        if not is_folder:
            self._validate_node_name(unique_id)
            self._validate_node_payload(data)

        if _supports_transactional_update(self.config):
            def _mut(cfg):
                all_nodes = cfg._getallnodes_unlocked()
                all_folders = cfg._getallfolders_unlocked()
                if is_folder:
                    if unique_id in all_folders:
                        raise NodeAlreadyExistsError(f"Folder '{unique_id}' already exists.")
                    uniques = cfg._explode_unique_unlocked(unique_id)
                    if not uniques:
                        raise InvalidConfigurationError(f"Invalid folder name '{unique_id}'.")
                    if "subfolder" in uniques:
                        parent_folder = f"@{uniques['folder']}"
                        if parent_folder not in all_folders:
                            raise NodeNotFoundError(f"Folder '{parent_folder}' not found.")
                    cfg._folder_add_unlocked(**uniques)
                else:
                    if unique_id in all_nodes:
                        raise NodeAlreadyExistsError(f"Node '{unique_id}' already exists.")
                    node_folder = unique_id.partition("@")[2]
                    if node_folder:
                        parent_folder = f"@{node_folder}"
                        if parent_folder not in all_folders:
                            raise NodeNotFoundError(f"Folder '{parent_folder}' not found.")
                    payload = deepcopy(data)
                    if "id" not in payload:
                        uniques = cfg._explode_unique_unlocked(unique_id)
                        if uniques and "id" in uniques:
                            payload["id"] = uniques["id"]
                    cfg._connections_add_unlocked(**payload)

            self.config.transactional_update(_mut)
            return

        all_nodes = self.config._getallnodes()
        all_folders = self.config._getallfolders()

        if is_folder:
            if unique_id in all_folders:
                raise NodeAlreadyExistsError(f"Folder '{unique_id}' already exists.")
            uniques = self.config._explode_unique(unique_id)
            if not uniques:
                raise InvalidConfigurationError(f"Invalid folder name '{unique_id}'.")

            if "subfolder" in uniques:
                self.validate_parent_folder(unique_id, is_folder=True)

            self.config._folder_add(**uniques)
            self.config._saveconfig(self.config.file)
        else:
            if unique_id in all_nodes:
                raise NodeAlreadyExistsError(f"Node '{unique_id}' already exists.")

            self.validate_parent_folder(unique_id)

            payload = deepcopy(data)
            if "id" not in payload:
                uniques = self.config._explode_unique(unique_id)
                if uniques and "id" in uniques:
                    payload["id"] = uniques["id"]

            self.config._connections_add(**payload)
            self.config._saveconfig(self.config.file)

    def _build_update_payload(self, unique_id, data, existing, uniques):
        if isinstance(existing, dict):
            merged = deepcopy(existing)
            if isinstance(uniques, dict):
                merged.update({k: v for k, v in uniques.items() if v})
            merged.update(deepcopy(data))
            filtered = {k: v for k, v in merged.items() if k in self._CONNECTION_FIELDS}
        else:
            filtered = deepcopy(data)
            if "id" not in filtered and isinstance(uniques, dict) and "id" in uniques:
                filtered["id"] = uniques["id"]
        self._validate_node_payload(filtered)
        return filtered

    def update_node(self, unique_id, data, save=True):
        """Explicitly update an existing node."""
        if _supports_transactional_update(self.config) and save:
            def _mut(cfg):
                all_nodes = cfg._getallnodes_unlocked()
                if unique_id not in all_nodes:
                    raise NodeNotFoundError(f"Node '{unique_id}' not found.")
                uniques = cfg._explode_unique_unlocked(unique_id)
                existing = cfg.getitem_unlocked(unique_id, extract=False)
                payload = self._build_update_payload(unique_id, data, existing, uniques)
                cfg._connections_add_unlocked(**payload)

            self.config.transactional_update(_mut)
            return

        all_nodes = self.config._getallnodes()
        if unique_id not in all_nodes:
            raise NodeNotFoundError(f"Node '{unique_id}' not found.")

        uniques = self.config._explode_unique(unique_id)
        existing = self.config.getitem(unique_id, extract=False)
        payload = self._build_update_payload(unique_id, data, existing, uniques)
        self.config._connections_add(**payload)
        if save:
            self.config._saveconfig(self.config.file)

    def delete_node(self, unique_id, is_folder=False, save=True):
        """Logic for deleting a node or folder."""
        if _supports_transactional_update(self.config) and save:
            def _mut(cfg):
                uniques = cfg._explode_unique_unlocked(unique_id)
                if not uniques:
                    label = "Folder" if is_folder else "Node"
                    raise NodeNotFoundError(f"{label} '{unique_id}' not found or invalid.")
                if is_folder:
                    cfg._folder_del_unlocked(**uniques)
                else:
                    cfg._connections_del_unlocked(**uniques)

            self.config.transactional_update(_mut)
            return

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

        if save:
            self.config._saveconfig(self.config.file)

    def connect_node(self, unique_id, sftp=False, debug=False, logger=None):
        """Interact with a node directly."""
        from connpy.core import node

        from ..auth_utils import resolve_fallback_entries
        from .profile_service import ProfileService

        node_data = self.config.getitem(unique_id, extract=False)
        if not node_data:
            raise NodeNotFoundError(f"Node '{unique_id}' not found.")

        profile_service = ProfileService(self.config)
        resolved_data = profile_service.resolve_node_data(node_data)

        node_params = inspect.signature(node.__init__).parameters
        if "fallback" not in node_params and "fallback" in resolved_data:
            raw_fb = resolved_data.pop("fallback", None)
            if raw_fb not in (None, "", []):
                resolved_fb = resolve_fallback_entries(raw_fb, self.config.profiles)
                if resolved_fb:
                    tags_val = resolved_data.get("tags", "")
                    if isinstance(tags_val, dict):
                        tags_copy = deepcopy(tags_val)
                        tags_copy["_fallback"] = resolved_fb
                        resolved_data["tags"] = tags_copy
                    elif tags_val in ("", None):
                        resolved_data["tags"] = {"_fallback": resolved_fb}
                    else:
                        resolved_data["tags"] = {"_raw": tags_val, "_fallback": resolved_fb}

        n = node(unique_id, **resolved_data, config=self.config)
        if sftp:
            n.protocol = "sftp"

        n.interact(debug=debug, logger=logger)

    def move_node(self, src_id, dst_id, copy=False):
        """Move or copy a node."""
        self._validate_node_name(dst_id)

        if _supports_transactional_update(self.config):
            def _mut(cfg):
                try:
                    node_data = cfg.getitem_unlocked(src_id)
                except (KeyError, TypeError):
                    node_data = None
                if not node_data:
                    raise NodeNotFoundError(f"Source node '{src_id}' not found.")
                if dst_id in cfg._getallnodes_unlocked():
                    raise NodeAlreadyExistsError(f"Destination node '{dst_id}' already exists.")
                new_uniques = cfg._explode_unique_unlocked(dst_id)
                if not new_uniques:
                    raise InvalidConfigurationError(f"Invalid destination format '{dst_id}'.")
                new_node_data = deepcopy(node_data)
                new_node_data.update(new_uniques)
                cfg._connections_add_unlocked(**new_node_data)
                if not copy:
                    src_uniques = cfg._explode_unique_unlocked(src_id)
                    cfg._connections_del_unlocked(**src_uniques)

            self.config.transactional_update(_mut)
            return

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
        self._validate_node_payload(common_data)

        if _supports_transactional_update(self.config):
            def _build_entries(cfg):
                all_nodes = cfg._getallnodes_unlocked()
                planned = []
                for i, uid in enumerate(ids):
                    if uid in all_nodes:
                        continue
                    try:
                        self._validate_node_name(uid)
                    except ReservedNameError:
                        continue
                    host = hosts[i] if i < len(hosts) else hosts[0]
                    uniques = cfg._explode_unique_unlocked(uid)
                    if not uniques:
                        continue
                    node_data = deepcopy(common_data)
                    node_data.pop("ids", None)
                    node_data.pop("location", None)
                    node_data.update(uniques)
                    node_data["host"] = host
                    node_data["type"] = "connection"
                    planned.append(node_data)
                return planned

            with self.config._mutation_lock:
                if not _build_entries(self.config):
                    return 0

            def _mut(cfg):
                entries = _build_entries(cfg)
                for node_data in entries:
                    cfg._connections_add_unlocked(**node_data)
                return len(entries)

            return self.config.transactional_update(_mut)

        count = 0
        all_nodes = self.config._getallnodes()

        for i, uid in enumerate(ids):
            if uid in all_nodes:
                continue

            try:
                self._validate_node_name(uid)
            except ReservedNameError:
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
        from ..auth_utils import SanitizedAuthError

        if _supports_transactional_update(self.config):
            def _mut(cfg):
                cfg.connections.clear()
                cfg.connections.update(deepcopy(connections))
                cfg.profiles.clear()
                cfg.profiles.update(deepcopy(profiles))

            try:
                self.config.transactional_update(_mut)
            except (ValueError, TypeError, SanitizedAuthError) as exc:
                raise InvalidConfigurationError(str(exc)) from None
            return

        self.config.connections = connections
        self.config.profiles = profiles
        self.config._saveconfig(self.config.file)

    def get_inventory(self):
        """Return a full snapshot of connections and profiles."""
        return {
            "connections": self.config.connections,
            "profiles": self.config.profiles
        }
