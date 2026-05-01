from .base import BaseService
import yaml
import os
from copy import deepcopy
from .exceptions import InvalidConfigurationError, NodeNotFoundError, ReservedNameError
from ..configfile import NoAliasDumper


class ImportExportService(BaseService):
    """Business logic for YAML/JSON inventory import and export."""

    def export_to_file(self, file_path, folders=None):
        """Export nodes/folders to a YAML file."""
        if os.path.exists(file_path):
            raise InvalidConfigurationError(f"File '{file_path}' already exists.")
            
        data = self.export_to_dict(folders)
        try:
            with open(file_path, "w") as f:
                yaml.dump(data, f, Dumper=NoAliasDumper, default_flow_style=False)
        except OSError as e:
            raise InvalidConfigurationError(f"Failed to export to '{file_path}': {e}")

    def export_to_dict(self, folders=None):
        """Export nodes/folders to a dictionary."""
        if not folders:
            return deepcopy(self.config.connections)
        else:
            # Validate folders exist
            for f in folders:
                if f != "@" and f not in self.config._getallfolders():
                    raise NodeNotFoundError(f"Folder '{f}' not found.")
            
            flat = self.config._getallnodesfull(folders, extract=False)
            nested = {}
            for k, v in flat.items():
                uniques = self.config._explode_unique(k)
                if not uniques:
                    continue
                
                if "folder" in uniques and "subfolder" in uniques:
                    f_name = uniques["folder"]
                    s_name = uniques["subfolder"]
                    i_name = uniques["id"]
                    
                    if f_name not in nested:
                        nested[f_name] = {"type": "folder"}
                    if s_name not in nested[f_name]:
                        nested[f_name][s_name] = {"type": "subfolder"}
                        
                    nested[f_name][s_name][i_name] = v
                    
                elif "folder" in uniques:
                    f_name = uniques["folder"]
                    i_name = uniques["id"]
                    
                    if f_name not in nested:
                        nested[f_name] = {"type": "folder"}
                        
                    nested[f_name][i_name] = v
                else:
                    i_name = uniques["id"]
                    nested[i_name] = v
                    
            return nested

    def import_from_file(self, file_path):
        """Import nodes/folders from a YAML file."""
        if not os.path.exists(file_path):
            raise InvalidConfigurationError(f"File '{file_path}' does not exist.")
            
        try:
            with open(file_path, "r") as f:
                data = yaml.load(f, Loader=yaml.FullLoader)
            self.import_from_dict(data)
        except Exception as e:
            raise InvalidConfigurationError(f"Failed to read/parse import file: {e}")

    def import_from_dict(self, data):
        """Import nodes/folders from a dictionary."""
        if not isinstance(data, dict):
            raise InvalidConfigurationError("Invalid import data format: expected a dictionary of nodes.")

        def _traverse_import(node_data, current_folder='', current_subfolder=''):
            for k, v in node_data.items():
                if k == "type":
                    continue
                if isinstance(v, dict):
                    node_type = v.get("type", "connection")
                    if node_type == "folder":
                        self.config._folder_add(folder=k)
                        _traverse_import(v, current_folder=k, current_subfolder='')
                    elif node_type == "subfolder":
                        self.config._folder_add(folder=current_folder, subfolder=k)
                        _traverse_import(v, current_folder=current_folder, current_subfolder=k)
                    elif node_type == "connection":
                        unique_id = k
                        if current_subfolder:
                            unique_id = f"{k}@{current_subfolder}@{current_folder}"
                        elif current_folder:
                            unique_id = f"{k}@{current_folder}"
                        self._validate_node_name(unique_id)
                        
                        kwargs = deepcopy(v)
                        kwargs['id'] = k
                        kwargs['folder'] = current_folder
                        kwargs['subfolder'] = current_subfolder
                        
                        self.config._connections_add(**kwargs)
                else:
                    # Invalid format skip
                    pass

        _traverse_import(data)
        self.config._saveconfig(self.config.file)
