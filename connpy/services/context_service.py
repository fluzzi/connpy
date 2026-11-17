import re
from typing import Any

from ..auth_utils import (
    MAX_CONTEXT_PATTERN_LEN,
    MAX_CONTEXT_REGEXES,
    compile_active_context_patterns,
    is_default_all_context_cfg,
    match_active_context_patterns,
)
from .base import BaseService
from .exceptions import InvalidConfigurationError


class ContextService(BaseService):
    """Business logic for managing and applying regex-based contexts locally."""

    @staticmethod
    def _validate_regexes(regexes: Any) -> None:
        if not isinstance(regexes, list) or not (1 <= len(regexes) <= MAX_CONTEXT_REGEXES):
            raise ValueError(
                f"Context regexes must be a list of 1 to {MAX_CONTEXT_REGEXES} patterns"
            )
        for pat in regexes:
            if not isinstance(pat, str) or not (1 <= len(pat) <= MAX_CONTEXT_PATTERN_LEN):
                raise ValueError(
                    f"Each context regex must be a non-empty string up to {MAX_CONTEXT_PATTERN_LEN} characters"
                )
            try:
                re.compile(pat)
            except re.error as exc:
                raise ValueError(f"Invalid context regex {pat!r}: {exc}") from None

    @property
    def contexts(self) -> dict[str, list[str]]:
        return self.config.config.get("contexts", {"all": [".*"]})

    @property
    def current_context(self) -> str:
        return self.config.config.get("current_context", "all")

    def list_contexts(self) -> list[dict[str, Any]]:
        result = []
        for name in self.contexts.keys():
            result.append({
                "name": name,
                "active": (name == self.current_context),
                "regexes": self.contexts[name]
            })
        return result

    def add_context(self, name: str, regexes: list[str]):
        if not name.isalnum():
            raise ValueError("Context name must be alphanumeric")
        self._validate_regexes(regexes)

        ctxs = self.contexts
        if name in ctxs:
            raise ValueError(f"Context '{name}' already exists")

        ctxs[name] = list(regexes)
        self.config.config["contexts"] = ctxs
        self.config._saveconfig(self.config.file)

    def update_context(self, name: str, regexes: list[str]):
        if name == "all":
            raise ValueError("Cannot modify default context 'all'")
        self._validate_regexes(regexes)

        ctxs = self.contexts
        if name not in ctxs:
            raise ValueError(f"Context '{name}' does not exist")

        ctxs[name] = list(regexes)
        self.config.config["contexts"] = ctxs
        self.config._saveconfig(self.config.file)

    def delete_context(self, name: str):
        if name == "all":
            raise ValueError("Cannot delete default context 'all'")
        if name == self.current_context:
            raise ValueError(f"Cannot delete active context '{name}'")
        
        ctxs = self.contexts
        if name not in ctxs:
            raise ValueError(f"Context '{name}' does not exist")
        
        del ctxs[name]
        self.config.config["contexts"] = ctxs
        self.config._saveconfig(self.config.file)

    def set_active_context(self, name: str):
        if name not in self.contexts:
            raise ValueError(f"Context '{name}' does not exist")
        
        self.config.config["current_context"] = name
        self.config._saveconfig(self.config.file)

    def get_active_regexes(self) -> list[re.Pattern]:
        return compile_active_context_patterns(getattr(self.config, "config", None))

    def _match_any(self, node_name: str, patterns: list[re.Pattern]) -> bool:
        return match_active_context_patterns(node_name, patterns)

    def is_default_all_context(self) -> bool:
        return is_default_all_context_cfg(getattr(self.config, "config", None))

    def matches_active_context(self, name: str) -> bool:
        if self.is_default_all_context():
            return isinstance(name, str) and bool(name)
        if not isinstance(name, str) or not name:
            return False
        return match_active_context_patterns(name, self.get_active_regexes())

    def validate_in_active_context(self, unique_id: str, is_folder: bool = False) -> None:
        if not self.is_default_all_context() and not self.matches_active_context(unique_id):
            label = "Folder" if is_folder else "Node"
            raise InvalidConfigurationError(
                f"{label} '{unique_id}' does not match active context '{self.current_context}'."
            )

    # Hook handlers for filtering
    def filter_node_list(self, *args, **kwargs):
        patterns = self.get_active_regexes()
        return [node for node in kwargs["result"] if self._match_any(node, patterns)]

    def filter_node_dict(self, *args, **kwargs):
        patterns = self.get_active_regexes()
        return {k: v for k, v in kwargs["result"].items() if self._match_any(k, patterns)}
