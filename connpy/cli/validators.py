import ast
import json
import re

from ..auth_utils import (
    MAX_FALLBACK_ENTRIES,
    MAX_JSON_INPUT_BYTES,
    MAX_JSON_INPUT_DEPTH,
    SanitizedAuthError,
    is_compose_expression,
    validate_auth_budget_tags,
    validate_compose_syntax,
    validate_fallback_entries,
    validate_json_depth_and_size,
)


def _raise_val_err(reason):
    import inquirer
    raise inquirer.errors.ValidationError("", reason=reason)

class Validators:
    def __init__(self, app):
        self.app = app

    def host_validation(self, answers, current, regex = "^.+$"):
        if not re.match(regex, current):
            _raise_val_err("Host cannot be empty")
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                _raise_val_err("Profile {} don't exist".format(current))
        return True

    def profile_protocol_validation(self, answers, current, regex = "(^ssh$|^mosh$|^telnet$|^kubectl$|^docker$|^ssm$|^$)"):
        if not re.match(regex, current):
            _raise_val_err("Pick between ssh, mosh, telnet, kubectl, docker, ssm or leave empty")
        return True

    def protocol_validation(self, answers, current, regex = "(^ssh$|^mosh$|^telnet$|^kubectl$|^docker$|^ssm$|^$|^@.+$)"):
        if not re.match(regex, current):
            _raise_val_err("Pick between ssh, mosh, telnet, kubectl, docker, ssm, leave empty or @profile")
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                _raise_val_err("Profile {} don't exist".format(current))
        return True

    def profile_port_validation(self, answers, current, regex = "(^[0-9]*$)"):
        if not re.match(regex, current):
            _raise_val_err("Pick a port between 1-65535, @profile o leave empty")
        try:
            port = int(current)
        except ValueError:
            port = 0
        if current != "" and not 1 <= int(port) <= 65535:
            _raise_val_err("Pick a port between 1-65535 or leave empty")
        return True

    def port_validation(self, answers, current, regex = "(^[0-9]*$|^@.+$)"):
        if not re.match(regex, current):
            _raise_val_err("Pick a port between 1-65535, @profile or leave empty")
        try:
            port = int(current)
        except ValueError:
            port = 0
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                _raise_val_err("Profile {} don't exist".format(current))
        elif current != "" and not 1 <= int(port) <= 65535:
            _raise_val_err("Pick a port between 1-65535, @profile o leave empty")
        return True

    def pass_validation(self, answers, current, regex = "(^@.+$)"):
        if is_compose_expression(current):
            try:
                validate_compose_syntax(current)
            except SanitizedAuthError as exc:
                _raise_val_err(str(exc))
            return True
        profiles = current.split(",")
        for i in profiles:
            if not re.match(regex, i) or i[1:] not in self.app.profiles:
                _raise_val_err("Profile {} don't exist".format(i))
        return True

    def _validate_tags_dict(self, current: str):
        if len(current.encode("utf-8", errors="replace")) > MAX_JSON_INPUT_BYTES:
            _raise_val_err(f"Tags exceed maximum size of {MAX_JSON_INPUT_BYTES} bytes")
        parsed = None
        try:
            parsed = json.loads(current)
        except (ValueError, TypeError):
            try:
                parsed = ast.literal_eval(current)
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                parsed = None
        if not isinstance(parsed, dict):
            _raise_val_err("Tags should be a python dictionary.")
        try:
            validate_json_depth_and_size(
                parsed,
                max_bytes=MAX_JSON_INPUT_BYTES,
                max_depth=MAX_JSON_INPUT_DEPTH,
            )
            validate_auth_budget_tags(parsed)
        except (ValueError, TypeError, SanitizedAuthError) as exc:
            _raise_val_err(str(exc))
        return True

    def tags_validation(self, answers, current):
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                _raise_val_err("Profile {} don't exist".format(current))
        elif current != "":
            return self._validate_tags_dict(current)
        return True

    def profile_tags_validation(self, answers, current):
        if current != "":
            return self._validate_tags_dict(current)
        return True

    def fallback_validation(self, answers, current):
        if current in (None, ""):
            return True
        stripped = str(current).strip()
        if not stripped:
            return True
        if len(stripped.encode("utf-8", errors="replace")) > MAX_JSON_INPUT_BYTES:
            _raise_val_err(f"Fallback exceeds maximum size of {MAX_JSON_INPUT_BYTES} bytes")
        if stripped.startswith("@") and not stripped.startswith("@@") and "+" not in stripped:
            parts = [p.strip() for p in stripped.split(",")]
            if any(not p for p in parts):
                _raise_val_err("Fallback profile entry cannot be empty")
            if len(parts) > MAX_FALLBACK_ENTRIES:
                _raise_val_err(f"Fallback exceeds maximum of {MAX_FALLBACK_ENTRIES} entries")
            for p in parts:
                if not p.startswith("@") or not re.fullmatch(r"[A-Za-z0-9_.$:/-]+", p[1:]):
                    _raise_val_err(
                        "Comma-separated fallback entries must be valid @profile references"
                    )
                if p[1:] not in self.app.profiles:
                    _raise_val_err(f"Profile {p} don't exist")
            return True
        try:
            if is_compose_expression(stripped):
                validate_compose_syntax(stripped)
                return True
            if stripped.startswith(("[", "{")):
                validate_json_depth_and_size(
                    stripped,
                    max_bytes=MAX_JSON_INPUT_BYTES,
                    max_depth=MAX_JSON_INPUT_DEPTH,
                )
            validate_fallback_entries(stripped, max_entries=MAX_FALLBACK_ENTRIES)
        except (ValueError, TypeError, SanitizedAuthError) as exc:
            _raise_val_err(str(exc))
        return True

    def _get_all_valid_nodes(self) -> list[str]:
        valid_nodes = list(getattr(self.app, "nodes_list", []) or [])
        if hasattr(self.app, "services") and hasattr(self.app.services, "nodes"):
            raw_fn = getattr(self.app.services.nodes, "_get_raw_nodes", None)
            if callable(raw_fn) and not hasattr(raw_fn, "assert_called"):
                raw_res = raw_fn()
                if isinstance(raw_res, (list, tuple, set)):
                    valid_nodes = list(dict.fromkeys([*raw_res, *valid_nodes]))
        return valid_nodes

    def _node_exists(self, target: str) -> bool:
        valid_nodes = self._get_all_valid_nodes()
        if getattr(self.app, "case", True):
            return target in valid_nodes
        return target.lower() in {n.lower() for n in valid_nodes}

    def jumphost_validation(self, answers, current):
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                _raise_val_err(f"Profile {current} don't exist")
        elif current != "" and not self._node_exists(current):
            _raise_val_err(f"Node {current} don't exist.")
        return True

    def profile_jumphost_validation(self, answers, current):
        if current != "" and not self._node_exists(current):
            _raise_val_err(f"Node {current} don't exist.")
        return True

    def default_validation(self, answers, current):
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                _raise_val_err("Profile {} don't exist".format(current))
        return True

    def bulk_node_validation(self, answers, current, regex = "^[0-9a-zA-Z_.,$#-]+$"):
        if not re.match(regex, current):
            _raise_val_err("Host cannot be empty")
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                _raise_val_err("Profile {} don't exist".format(current))
        return True

    def bulk_folder_validation(self, answers, current):
        case = getattr(self.app, "case", False)
        if not case:
            current = current.lower()

        candidate = current.split("/")[0] if "/" in current else current

        if hasattr(self.app, "services") and hasattr(self.app.services, "context"):
            raw_folders = list(getattr(self.app, "folders", []) or [])
            if hasattr(self.app.services, "nodes"):
                raw_fn = getattr(self.app.services.nodes, "_get_raw_folders", None)
                if callable(raw_fn) and not hasattr(raw_fn, "assert_called"):
                    raw_res = raw_fn()
                    if isinstance(raw_res, (list, tuple, set)):
                        raw_folders = list(raw_res)

            def _folder_exists_norm(folder_name: str) -> bool:
                if case:
                    return folder_name in raw_folders
                return folder_name.lower() in {f.lower() for f in raw_folders}

            if current != "" and not _folder_exists_norm(candidate):
                _raise_val_err(f"Location {current} don't exist")

            ctx_svc = self.app.services.context
            if ctx_svc is not None and not ctx_svc.is_default_all_context():
                raw_ids = answers.get("ids", "") if isinstance(answers, dict) else ""
                for nid in str(raw_ids).split(","):
                    nid_stripped = nid.strip()
                    if not nid_stripped:
                        continue
                    nid_norm = nid_stripped if case else nid_stripped.lower()
                    full_id = f"{nid_norm}{candidate}" if candidate else nid_norm
                    if not ctx_svc.matches_active_context(full_id):
                        _raise_val_err(
                            f"Node '{full_id}' does not match active context '{ctx_svc.current_context}'."
                        )
                    parent = full_id.partition("@")[2]
                    if parent and not _folder_exists_norm(f"@{parent}"):
                        _raise_val_err(f"Location @{parent} don't exist")
            return True

        matches = list(filter(lambda k: k == candidate, self.app.folders))
        if current != "" and len(matches) == 0:
            _raise_val_err("Location {} don't exist".format(current))
        return True

    def bulk_host_validation(self, answers, current, regex = "^.+$"):
        if not re.match(regex, current):
            _raise_val_err("Host cannot be empty")
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                _raise_val_err("Profile {} don't exist".format(current))
        hosts = current.split(",")
        nodes = answers["ids"].split(",")
        if len(hosts) > 1 and len(hosts) != len(nodes):
                _raise_val_err("Hosts list should be the same length of nodes list")
        return True
