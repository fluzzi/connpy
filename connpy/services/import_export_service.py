import os
import sys
from copy import deepcopy

import yaml

from ..configfile import NoAliasDumper
from .base import BaseService
from .exceptions import InvalidConfigurationError, NodeNotFoundError
from .node_service import NodeService, _supports_transactional_update


class ImportExportService(BaseService):
    """Business logic for YAML/JSON inventory import and export."""

    def __init__(self, config=None, enforce_context: bool = True):
        super().__init__(config)
        self.enforce_context = bool(enforce_context)

    def _get_context_service(self, cfg=None):
        target_cfg = cfg or self.config
        if (
            not getattr(self, "enforce_context", True)
            or not getattr(target_cfg, "_enforce_context", True)
            or (len(sys.argv) > 1 and sys.argv[1] == "api")
            or not isinstance(getattr(target_cfg, "config", None), dict)
        ):
            return None
        from .context_service import ContextService

        return ContextService(target_cfg)

    def _get_raw_folders(self, cfg=None) -> list[str]:
        target_cfg = cfg or self.config
        if target_cfg is None:
            return []
        if (
            not hasattr(target_cfg, "assert_called")
            and callable(getattr(target_cfg, "_getallfolders_unlocked", None))
            and hasattr(target_cfg, "_mutation_lock")
        ):
            with target_cfg._mutation_lock:
                return target_cfg._getallfolders_unlocked()
        return target_cfg._getallfolders()

    def _folder_exists(self, folder_id: str, raw_folders: list[str], cfg=None) -> bool:
        target_cfg = cfg or self.config
        cfg_dict = getattr(target_cfg, "config", None)
        case = cfg_dict.get("case", False) if isinstance(cfg_dict, dict) else False
        if case:
            return folder_id in raw_folders
        return folder_id.lower() in {f.lower() for f in raw_folders}

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
        ctx_svc = self._get_context_service()
        ctx_active = ctx_svc is not None and not ctx_svc.is_default_all_context()

        if not folders and not ctx_active:
            return deepcopy(self.config.connections)

        raw_folders = self._get_raw_folders()
        if folders:
            for f in folders:
                if f != "@" and (
                    not self._folder_exists(f, raw_folders)
                    or (ctx_active and not ctx_svc.matches_active_context(f))
                ):
                    raise NodeNotFoundError(f"Folder '{f}' not found.")
            flat = self.config._getallnodesfull(folders, extract=False)
            allowed_folders = []
        else:
            allowed_folders = ctx_svc.filter_node_list(result=raw_folders)
            flat = self.config._getallnodesfull(extract=False)

        if ctx_active:
            flat = ctx_svc.filter_node_dict(result=flat)

        nested = {}
        for folder_id in allowed_folders:
            uniques = self.config._explode_unique(folder_id)
            if not uniques or "folder" not in uniques:
                continue
            f_name = uniques["folder"]
            if f_name not in nested:
                nested[f_name] = {"type": "folder"}
            if "subfolder" in uniques:
                s_name = uniques["subfolder"]
                if s_name not in nested[f_name]:
                    nested[f_name][s_name] = {"type": "subfolder"}

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
                data = yaml.safe_load(f)
            self.import_from_dict(data)
        except Exception as e:
            raise InvalidConfigurationError(f"Failed to read/parse import file: {e}")

    def import_from_dict(self, data):
        """Import nodes/folders from a dictionary atomically with context pre-validation."""
        if not isinstance(data, dict):
            raise InvalidConfigurationError(
                "Invalid import data format: expected a dictionary of nodes."
            )

        ctx_svc = self._get_context_service()
        ctx_active = ctx_svc is not None and not ctx_svc.is_default_all_context()
        raw_folders = self._get_raw_folders() if ctx_active else []
        planned_ops = []

        def _plan_import(node_data, current_folder="", current_subfolder=""):
            for k, v in node_data.items():
                if k == "type" or not isinstance(v, dict):
                    continue
                node_type = v.get("type", "connection")
                if node_type == "folder":
                    folder_id = f"@{k}"
                    has_children = any(
                        isinstance(cv, dict) for ck, cv in v.items() if ck != "type"
                    )
                    if ctx_active and (
                        not has_children
                        or (
                            not ctx_svc.matches_active_context(folder_id)
                            and not self._folder_exists(folder_id, raw_folders)
                        )
                    ):
                        ctx_svc.validate_in_active_context(folder_id, is_folder=True)
                    planned_ops.append(("folder", k, ""))
                    _plan_import(v, current_folder=k, current_subfolder="")
                elif node_type == "subfolder":
                    folder_id = f"@{k}@{current_folder}"
                    has_children = any(
                        isinstance(cv, dict) for ck, cv in v.items() if ck != "type"
                    )
                    if ctx_active and (
                        not has_children
                        or (
                            not ctx_svc.matches_active_context(folder_id)
                            and not self._folder_exists(folder_id, raw_folders)
                        )
                    ):
                        ctx_svc.validate_in_active_context(folder_id, is_folder=True)
                    planned_ops.append(("folder", current_folder, k))
                    _plan_import(v, current_folder=current_folder, current_subfolder=k)
                elif node_type == "connection":
                    unique_id = k
                    if current_subfolder:
                        unique_id = f"{k}@{current_subfolder}@{current_folder}"
                    elif current_folder:
                        unique_id = f"{k}@{current_folder}"
                    self._validate_node_name(unique_id)
                    NodeService._validate_node_payload(v)
                    if ctx_svc is not None:
                        ctx_svc.validate_in_active_context(unique_id, is_folder=False)

                    kwargs = deepcopy(v)
                    kwargs["id"] = k
                    kwargs["folder"] = current_folder
                    kwargs["subfolder"] = current_subfolder
                    planned_ops.append(("connection", kwargs))

        _plan_import(data)

        if _supports_transactional_update(self.config):
            def _mut(cfg):
                for op in planned_ops:
                    if op[0] == "folder":
                        cfg._folder_add_unlocked(folder=op[1], subfolder=op[2])
                    else:
                        cfg._connections_add_unlocked(**op[1])

            self.config.transactional_update(_mut)
            return

        snap_connections = deepcopy(self.config.connections)
        try:
            for op in planned_ops:
                if op[0] == "folder":
                    self.config._folder_add(folder=op[1], subfolder=op[2])
                else:
                    self.config._connections_add(**op[1])
            self.config._saveconfig(self.config.file)
        except BaseException:
            self.config.connections.clear()
            self.config.connections.update(snap_connections)
            raise
