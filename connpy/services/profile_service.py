from contextlib import suppress
from copy import deepcopy
from typing import ClassVar

from .base import BaseService
from .exceptions import (
    InvalidConfigurationError,
    ProfileAlreadyExistsError,
    ProfileNotFoundError,
)

MAX_PROFILE_DEPTH = 5


def _supports_transactional_update(cfg):
    return (
        cfg is not None
        and not hasattr(cfg, "assert_called")
        and callable(getattr(cfg, "transactional_update", None))
    )


class ProfileService(BaseService):
    """Business logic for node profiles management."""

    _ALLOWED_KEYS: ClassVar[frozenset[str]] = frozenset({
        "host",
        "options",
        "logs",
        "password",
        "port",
        "protocol",
        "user",
        "tags",
        "jumphost",
        "fallback",
    })

    @staticmethod
    def _validate_profile_payload(data):
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

    def _get_profiles_snapshot(self):
        lock = getattr(self.config, "_mutation_lock", None)
        if lock is not None and not hasattr(self.config, "assert_called"):
            with lock:
                return deepcopy(self.config.profiles)
        return deepcopy(self.config.profiles)

    def list_profiles(self, filter_str=None):
        """List all profile names, optionally filtered."""
        profiles = list(self._get_profiles_snapshot().keys())
        case_sensitive = self.config.config.get("case", False)

        if filter_str:
            if not case_sensitive:
                f_str = filter_str.lower()
                return [p for p in profiles if f_str in p.lower()]
            return [p for p in profiles if filter_str in p]
        return profiles

    def get_profile(self, name, resolve=True, *, _visited=frozenset(), _depth=0):
        """Get the profile dictionary, optionally resolved."""
        if _depth >= MAX_PROFILE_DEPTH or name in _visited:
            raise InvalidConfigurationError(
                f"Circular or overly deep profile reference involving '{name}'."
            )
        profiles = self._get_profiles_snapshot()
        profile = profiles.get(name)
        if not profile:
            raise ProfileNotFoundError(f"Profile '{name}' not found.")

        if resolve:
            return self.resolve_node_data(
                profile,
                _visited=_visited | {name},
                _depth=_depth + 1,
                _profiles_snapshot=profiles,
            )
        return profile

    def add_profile(self, name, data):
        """Add a new profile."""
        filtered_data = {k: deepcopy(v) for k, v in data.items() if k in self._ALLOWED_KEYS}
        self._validate_profile_payload(filtered_data)

        if _supports_transactional_update(self.config):
            def _mut(cfg):
                if name in cfg.profiles:
                    raise ProfileAlreadyExistsError(f"Profile '{name}' already exists.")
                cfg._profiles_add_unlocked(id=name, **filtered_data)

            self.config.transactional_update(_mut)
            return

        if name in self.config.profiles:
            raise ProfileAlreadyExistsError(f"Profile '{name}' already exists.")
        self.config._profiles_add(id=name, **filtered_data)
        self.config._saveconfig(self.config.file)

    def resolve_node_data(
        self,
        node_data,
        *,
        _visited=frozenset(),
        _depth=0,
        _profiles_snapshot=None,
    ):
        """Resolve profile references (@profile) in node data while preserving compose and scalar secrets."""
        from ..auth_utils import is_compose_expression

        if _depth > MAX_PROFILE_DEPTH:
            raise InvalidConfigurationError("Profile inheritance exceeds maximum depth.")

        profiles = (
            _profiles_snapshot
            if _profiles_snapshot is not None
            else self._get_profiles_snapshot()
        )
        resolved = deepcopy(node_data)

        referenced_profiles = []
        for key, value in resolved.items():
            if key == "fallback":
                continue
            if isinstance(value, str):
                if not is_compose_expression(value) and value.startswith("@"):
                    ref = value[1:]
                    if key != "password" or ref in profiles:
                        referenced_profiles.append(ref)
            elif isinstance(value, list):
                for item in value:
                    if (
                        isinstance(item, str)
                        and not is_compose_expression(item)
                        and item.startswith("@")
                    ):
                        ref = item[1:]
                        if key != "password" or ref in profiles:
                            referenced_profiles.append(ref)

        def _get_resolved_ref(prof_name):
            if prof_name in _visited or _depth >= MAX_PROFILE_DEPTH:
                raise InvalidConfigurationError(
                    f"Circular or overly deep profile reference involving '{prof_name}'."
                )
            prof_raw = profiles.get(prof_name)
            if not prof_raw:
                raise ProfileNotFoundError(f"Profile '{prof_name}' not found.")
            return self.resolve_node_data(
                prof_raw,
                _visited=_visited | {prof_name},
                _depth=_depth + 1,
                _profiles_snapshot=profiles,
            )

        for key, value in list(resolved.items()):
            if key == "fallback":
                continue
            if key == "password":
                if isinstance(value, str):
                    if is_compose_expression(value):
                        continue
                    if value.startswith("@") and value[1:] in profiles:
                        prof_obj = _get_resolved_ref(value[1:])
                        resolved["password"] = prof_obj.get("password", "")
                elif isinstance(value, list):
                    resolved_list = []
                    for item in value:
                        if (
                            isinstance(item, str)
                            and not is_compose_expression(item)
                            and item.startswith("@")
                            and item[1:] in profiles
                        ):
                            with suppress(ProfileNotFoundError):
                                prof_obj = _get_resolved_ref(item[1:])
                                if "password" in prof_obj:
                                    resolved_list.append(prof_obj["password"])
                        else:
                            resolved_list.append(item)
                    resolved["password"] = resolved_list
                continue

            if isinstance(value, str) and value.startswith("@"):
                profile_name = value[1:]
                try:
                    profile = _get_resolved_ref(profile_name)
                    resolved[key] = profile.get(key, "")
                except ProfileNotFoundError:
                    resolved[key] = ""
            elif isinstance(value, list):
                resolved_list = []
                for item in value:
                    if isinstance(item, str) and item.startswith("@"):
                        profile_name = item[1:]
                        with suppress(ProfileNotFoundError):
                            profile = _get_resolved_ref(profile_name)
                            if "password" in profile:
                                resolved_list.append(profile["password"])
                    else:
                        resolved_list.append(item)
                resolved[key] = resolved_list

        if referenced_profiles:
            base_profile_name = referenced_profiles[0]
            with suppress(ProfileNotFoundError):
                base_profile = _get_resolved_ref(base_profile_name)
                for key, value in base_profile.items():
                    if key not in resolved or resolved[key] in ("", [], None):
                        resolved[key] = deepcopy(value)

        if resolved.get("protocol") in ("", None):
            try:
                default_profile = _get_resolved_ref("default")
                resolved["protocol"] = default_profile.get("protocol", "ssh") or "ssh"
            except ProfileNotFoundError:
                resolved["protocol"] = "ssh"

        return resolved

    def delete_profile(self, name):
        """Delete an existing profile, with safety checks."""
        if _supports_transactional_update(self.config):
            def _mut(cfg):
                if name not in cfg.profiles:
                    raise ProfileNotFoundError(f"Profile '{name}' not found.")
                if name == "default":
                    raise InvalidConfigurationError("Cannot delete the 'default' profile.")
                used_by = cfg._profileused_unlocked(name)
                if used_by:
                    raise InvalidConfigurationError(
                        f"Profile '{name}' is used by nodes: {', '.join(used_by)}"
                    )
                cfg._profiles_del_unlocked(id=name)

            self.config.transactional_update(_mut)
            return

        if name not in self.config.profiles:
            raise ProfileNotFoundError(f"Profile '{name}' not found.")
        if name == "default":
            raise InvalidConfigurationError("Cannot delete the 'default' profile.")
        used_by = self.config._profileused(name)
        if used_by:
            raise InvalidConfigurationError(
                f"Profile '{name}' is used by nodes: {', '.join(used_by)}"
            )
        self.config._profiles_del(id=name)
        self.config._saveconfig(self.config.file)

    def update_profile(self, name, data):
        """Update an existing profile."""
        if _supports_transactional_update(self.config):
            def _mut(cfg):
                if name not in cfg.profiles:
                    raise ProfileNotFoundError(f"Profile '{name}' not found.")
                updated_data = deepcopy(cfg.profiles[name])
                updated_data.update(deepcopy(data))
                filtered_data = {
                    k: v for k, v in updated_data.items() if k in self._ALLOWED_KEYS
                }
                self._validate_profile_payload(filtered_data)
                cfg._profiles_add_unlocked(id=name, **filtered_data)

            self.config.transactional_update(_mut)
            return

        if name not in self.config.profiles:
            raise ProfileNotFoundError(f"Profile '{name}' not found.")
        existing = self.get_profile(name, resolve=False)
        updated_data = deepcopy(existing)
        updated_data.update(data)
        filtered_data = {k: v for k, v in updated_data.items() if k in self._ALLOWED_KEYS}
        self._validate_profile_payload(filtered_data)
        self.config._profiles_add(id=name, **filtered_data)
        self.config._saveconfig(self.config.file)

