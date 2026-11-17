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

    def jumphost_validation(self, answers, current):
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                _raise_val_err("Profile {} don't exist".format(current))
        elif current != "":
            if current not in self.app.nodes_list:
                _raise_val_err("Node {} don't exist.".format(current))
        return True

    def profile_jumphost_validation(self, answers, current):
        if current != "":
            if current not in self.app.nodes_list:
                _raise_val_err("Node {} don't exist.".format(current))
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
        if not self.app.case:
            current = current.lower()
            
        candidate = current
        if "/" in current:
            candidate = current.split("/")[0]
            
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
