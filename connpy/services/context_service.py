import re
from typing import List, Dict, Any
from .base import BaseService
from ..hooks import MethodHook
from .. import printer

class ContextService(BaseService):
    """Business logic for managing and applying regex-based contexts locally."""

    @property
    def contexts(self) -> Dict[str, List[str]]:
        return self.config.config.get("contexts", {"all": [".*"]})

    @property
    def current_context(self) -> str:
        return self.config.config.get("current_context", "all")

    def list_contexts(self) -> List[Dict[str, Any]]:
        result = []
        for name in self.contexts.keys():
            result.append({
                "name": name,
                "active": (name == self.current_context),
                "regexes": self.contexts[name]
            })
        return result

    def add_context(self, name: str, regexes: List[str]):
        if not name.isalnum():
            raise ValueError("Context name must be alphanumeric")
        
        ctxs = self.contexts
        if name in ctxs:
            raise ValueError(f"Context '{name}' already exists")
        
        ctxs[name] = regexes
        self.config.config["contexts"] = ctxs
        self.config._saveconfig(self.config.file)

    def update_context(self, name: str, regexes: List[str]):
        if name == "all":
            raise ValueError("Cannot modify default context 'all'")
        
        ctxs = self.contexts
        if name not in ctxs:
            raise ValueError(f"Context '{name}' does not exist")
        
        ctxs[name] = regexes
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

    def get_active_regexes(self) -> List[re.Pattern]:
        patterns = self.contexts.get(self.current_context, [".*"])
        return [re.compile(p) for p in patterns]

    def _match_any(self, node_name: str, patterns: List[re.Pattern]) -> bool:
        return any(p.match(node_name) for p in patterns)

    # Hook handlers for filtering
    def filter_node_list(self, *args, **kwargs):
        patterns = self.get_active_regexes()
        return [node for node in kwargs["result"] if self._match_any(node, patterns)]

    def filter_node_dict(self, *args, **kwargs):
        patterns = self.get_active_regexes()
        return {k: v for k, v in kwargs["result"].items() if self._match_any(k, patterns)}
