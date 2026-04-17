from .base import BaseService
import yaml
import os
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
            return self.config._getallnodesfull(extract=False)
        else:
            # Validate folders exist
            for f in folders:
                if f != "@" and f not in self.config._getallfolders():
                    raise NodeNotFoundError(f"Folder '{f}' not found.")
            return self.config._getallnodesfull(folders, extract=False)

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

        # Process imports
        for k, v in data.items():
            uniques = self.config._explode_unique(k)
            
            # Ensure folders exist
            if "folder" in uniques:
                folder_name = f"@{uniques['folder']}"
                if folder_name not in self.config._getallfolders():
                    folder_uniques = self.config._explode_unique(folder_name)
                    self.config._folder_add(**folder_uniques)
            
            if "subfolder" in uniques:
                sub_name = f"@{uniques['subfolder']}@{uniques['folder']}"
                if sub_name not in self.config._getallfolders():
                    sub_uniques = self.config._explode_unique(sub_name)
                    self.config._folder_add(**sub_uniques)
            
            # Add node/connection
            v.update(uniques)
            self._validate_node_name(k)
            self.config._connections_add(**v)
            
        self.config._saveconfig(self.config.file)
