import inspect
import os
import re
import sys
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from connpy.auth_utils import (
    MAX_REMOTE_FILTER_NODES,
    AuthAttemptTracker,
    raise_sanitized_auth_error,
)
from connpy.core import nodes as Nodes

from .base import BaseService
from .exceptions import ConnpyError


class ExecutionService(BaseService):
    """Business logic for executing commands on nodes and running automation scripts."""

    def __init__(
        self,
        config=None,
        remote_stub=None,
        attempt_tracker=None,
        enforce_context: bool = True,
    ):
        super().__init__(config)
        self.remote_stub = remote_stub
        self.attempt_tracker = (
            attempt_tracker if attempt_tracker is not None else AuthAttemptTracker()
        )
        self.enforce_context = bool(enforce_context)
        self.last_fallback_used: dict[str, bool] = {}

    def _get_context_service(self):
        if (
            not getattr(self, "enforce_context", True)
            or not getattr(self.config, "_enforce_context", True)
            or (len(sys.argv) > 1 and sys.argv[1] == "api")
            or not isinstance(getattr(self.config, "config", None), dict)
        ):
            return None
        from .context_service import ContextService

        return ContextService(self.config)

    def _is_remote_backend(self) -> bool:
        """Return True when execution is configured to run against a remote gRPC backend."""
        if getattr(self, "remote_stub", None) is not None:
            return True
        cfg = getattr(self.config, "config", None)
        return isinstance(cfg, dict) and cfg.get("service_mode") == "remote"

    def _ensure_backend_supports_stage_timeouts(
        self,
        connect_timeout: int | None = None,
        auth_timeout: int | None = None,
    ) -> None:
        """Raise ConnpyError if per-stage timeout overrides are used on gRPC remote backend."""
        if self._is_remote_backend() and (connect_timeout is not None or auth_timeout is not None):
            raise ConnpyError(
                "connect_timeout and auth_timeout overrides are not supported in remote gRPC mode"
            )

    def _get_remote_channel(self):
        cfg = getattr(self.config, "config", None)
        remote_host = cfg.get("remote_host") if isinstance(cfg, dict) else None
        if not remote_host:
            raise ConnpyError("Remote host must be specified in remote mode")

        import grpc

        from connpy.grpc_layer.stubs import AuthClientInterceptor

        def get_token():
            env_token = os.environ.get("CONNPY_TOKEN")
            if env_token:
                return env_token
            defaultdir = getattr(self.config, "defaultdir", None)
            if defaultdir:
                token_path = os.path.join(defaultdir, ".token")
                if os.path.exists(token_path):
                    with suppress(OSError), open(token_path) as f:
                        return f.read().strip()
            return None

        channel = grpc.insecure_channel(remote_host)
        channel = grpc.intercept_channel(channel, AuthClientInterceptor(get_token))
        return channel, remote_host

    def _get_remote_stub(self):
        """Return the configured or lazily constructed ExecutionStub for remote gRPC mode."""
        if getattr(self, "remote_stub", None) is not None:
            return self.remote_stub
        from connpy.grpc_layer.stubs import ExecutionStub

        channel, remote_host = self._get_remote_channel()
        return ExecutionStub(channel, remote_host=remote_host)

    def _filter_remote_nodes_filter(self, nodes_filter):
        ctx_svc = self._get_context_service()
        if ctx_svc is None or ctx_svc.is_default_all_context():
            return nodes_filter

        if isinstance(nodes_filter, (list, tuple)):
            permitted = ctx_svc.filter_node_list(result=list(nodes_filter))
        else:
            if self.remote_stub is not None and callable(
                getattr(self.remote_stub, "list_nodes", None)
            ):
                candidates = self.remote_stub.list_nodes(filter_str=nodes_filter)
            elif self.remote_stub is not None and callable(
                getattr(getattr(self.remote_stub, "node_stub", None), "list_nodes", None)
            ):
                candidates = self.remote_stub.node_stub.list_nodes(filter_str=nodes_filter)
            elif self.remote_stub is None:
                from connpy.grpc_layer.stubs import NodeStub

                channel, remote_host = self._get_remote_channel()
                node_stub = NodeStub(channel, remote_host=remote_host, config=self.config)
                candidates = node_stub.list_nodes(filter_str=nodes_filter)
            else:
                candidates = (
                    self.config._getallnodes(nodes_filter)
                    if hasattr(self.config, "_getallnodes")
                    else []
                )
            if nodes_filter:
                cfg_dict = getattr(self.config, "config", None)
                case = cfg_dict.get("case", False) if isinstance(cfg_dict, dict) else False
                flags = 0 if case else re.IGNORECASE
                candidates = [c for c in candidates if re.search(nodes_filter, c, flags)]
            permitted = ctx_svc.filter_node_list(result=candidates)

        if not permitted:
            raise ConnpyError(f"No nodes found matching filter: {nodes_filter}")
        return [f"^{re.escape(n)}$" for n in permitted]

    def _build_remote_filter_batches(self, nodes_filter):
        filtered = self._filter_remote_nodes_filter(nodes_filter)
        if isinstance(filtered, list):
            return [
                filtered[i : i + MAX_REMOTE_FILTER_NODES]
                for i in range(0, len(filtered), MAX_REMOTE_FILTER_NODES)
            ]
        return [filtered]

    @staticmethod
    def _aggregate_remote_results(batch_results):
        if len(batch_results) == 1:
            return batch_results[0]
        if all(isinstance(r, dict) for r in batch_results):
            merged: dict[str, Any] = {}
            for r in batch_results:
                merged.update(r)
            return merged
        if all(isinstance(r, list) for r in batch_results):
            merged_list: list[Any] = []
            for r in batch_results:
                merged_list.extend(r)
            return merged_list
        return batch_results[-1]

    def run_commands(
        self,
        nodes_filter: str,
        commands: list[str],
        variables: dict[str, Any] | None = None,
        parallel: int = 10,
        timeout: int | None = None,
        folder: str | None = None,
        prompt: str | None = None,
        on_node_complete: Callable | None = None,
        logger: Callable | None = None,
        name: str | None = None,
        connect_timeout: int | None = None,
        auth_timeout: int | None = None,
    ) -> dict[str, Any]:
        """Execute commands on a set of nodes."""
        try:
            self._ensure_backend_supports_stage_timeouts(
                connect_timeout=connect_timeout,
                auth_timeout=auth_timeout,
            )

            if self._is_remote_backend():
                batches = self._build_remote_filter_batches(nodes_filter)
                stub = self._get_remote_stub()
                batch_results = [
                    stub.run_commands(
                        nodes_filter=batch,
                        commands=commands,
                        variables=variables,
                        parallel=parallel,
                        timeout=timeout if timeout is not None else 20,
                        folder=folder,
                        prompt=prompt,
                        on_node_complete=on_node_complete,
                        logger=logger,
                        name=name or "",
                    )
                    for batch in batches
                ]
                return self._aggregate_remote_results(batch_results)

            matched_names = self.config._getallnodes(nodes_filter)
            ctx_svc = self._get_context_service()
            if ctx_svc is not None and not ctx_svc.is_default_all_context():
                matched_names = ctx_svc.filter_node_list(result=matched_names)
            if not matched_names:
                raise ConnpyError(f"No nodes found matching filter: {nodes_filter}")

            node_data = self.config.getitems(matched_names, extract=True)
            executor = Nodes(node_data, config=self.config)
            self.last_executor = executor

            run_kwargs: dict[str, Any] = {
                "commands": commands,
                "vars": variables,
                "parallel": parallel,
                "timeout": timeout,
                "folder": folder,
                "prompt": prompt,
                "on_complete": on_node_complete,
                "logger": logger,
                "connect_timeout": connect_timeout,
                "auth_timeout": auth_timeout,
                "_default_cmd_timeout": 20,
            }
            run_params = inspect.signature(executor.run).parameters
            if "unattended" in run_params:
                run_kwargs["unattended"] = True
            if "attempt_tracker" in run_params:
                run_kwargs["attempt_tracker"] = self.attempt_tracker

            results = executor.run(**run_kwargs)

            fb_map = getattr(executor, "fallback_used", {})
            fb_dict = fb_map if isinstance(fb_map, dict) else {}

            # Combine output, status, and fallback metadata for the caller
            full_results = {}
            self.last_fallback_used = {}
            for unique in results:
                fb_used = bool(fb_dict.get(unique, False))
                full_results[unique] = {
                    "output": results[unique],
                    "status": executor.status.get(unique, 1),
                    "fallback_used": fb_used,
                }
                self.last_fallback_used[unique] = fb_used

            return full_results
        except ConnpyError:
            raise
        except Exception as e:  # noqa: BLE001
            raise_sanitized_auth_error(e, code="AUTH_FAILED", context_label="execution failed")

    def test_commands(
        self,
        nodes_filter: str,
        commands: list[str],
        expected: list[str],
        variables: dict[str, Any] | None = None,
        parallel: int = 10,
        timeout: int | None = None,
        folder: str | None = None,
        prompt: str | None = None,
        on_node_complete: Callable | None = None,
        logger: Callable | None = None,
        name: str | None = None,
        connect_timeout: int | None = None,
        auth_timeout: int | None = None,
    ) -> dict[str, dict[str, bool]]:
        """Run commands and verify expected output on a set of nodes."""
        try:
            self._ensure_backend_supports_stage_timeouts(
                connect_timeout=connect_timeout,
                auth_timeout=auth_timeout,
            )

            if self._is_remote_backend():
                batches = self._build_remote_filter_batches(nodes_filter)
                stub = self._get_remote_stub()
                batch_results = [
                    stub.test_commands(
                        nodes_filter=batch,
                        commands=commands,
                        expected=expected,
                        variables=variables,
                        parallel=parallel,
                        timeout=timeout if timeout is not None else 20,
                        folder=folder or "",
                        prompt=prompt,
                        on_node_complete=on_node_complete,
                        logger=logger,
                        name=name or "",
                    )
                    for batch in batches
                ]
                return self._aggregate_remote_results(batch_results)

            matched_names = self.config._getallnodes(nodes_filter)
            ctx_svc = self._get_context_service()
            if ctx_svc is not None and not ctx_svc.is_default_all_context():
                matched_names = ctx_svc.filter_node_list(result=matched_names)
            if not matched_names:
                raise ConnpyError(f"No nodes found matching filter: {nodes_filter}")

            node_data = self.config.getitems(matched_names, extract=True)
            executor = Nodes(node_data, config=self.config)
            self.last_executor = executor

            test_kwargs: dict[str, Any] = {
                "commands": commands,
                "expected": expected,
                "vars": variables,
                "parallel": parallel,
                "timeout": timeout,
                "folder": folder,
                "prompt": prompt,
                "on_complete": on_node_complete,
                "logger": logger,
                "connect_timeout": connect_timeout,
                "auth_timeout": auth_timeout,
                "_default_cmd_timeout": 20,
            }
            test_params = inspect.signature(executor.test).parameters
            if "unattended" in test_params:
                test_kwargs["unattended"] = True
            if "attempt_tracker" in test_params:
                test_kwargs["attempt_tracker"] = self.attempt_tracker

            res = executor.test(**test_kwargs)
            fb_map = getattr(executor, "fallback_used", {})
            fb_dict = fb_map if isinstance(fb_map, dict) else {}
            if isinstance(res, dict):
                self.last_fallback_used = {u: bool(fb_dict.get(u, False)) for u in res}
            else:
                self.last_fallback_used = {}
            return res
        except ConnpyError:
            raise
        except Exception as e:  # noqa: BLE001
            raise_sanitized_auth_error(e, code="AUTH_FAILED", context_label="testing failed")

    def run_cli_script(self, nodes_filter: str, script_path: str, parallel: int = 10) -> dict[str, str]:
        """Run a plain-text script containing one command per line."""
        if not os.path.exists(script_path):
            raise ConnpyError(f"Script file not found: {script_path}")

        try:
            with open(script_path) as f:
                commands = [line.strip() for line in f if line.strip()]
        except OSError:
            raise ConnpyError("Failed to read script file") from None

        return self.run_commands(nodes_filter, commands, parallel=parallel)

