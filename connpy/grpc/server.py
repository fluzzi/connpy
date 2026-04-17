import grpc
from concurrent import futures
from google.protobuf.empty_pb2 import Empty
import os
import ctypes
import threading

# Suppress harmless but noisy gRPC fork() warnings from pexpect child processes
os.environ["GRPC_VERBOSITY"] = "NONE"
os.environ["GRPC_ENABLE_FORK_SUPPORT"] = "0"
from . import connpy_pb2, connpy_pb2_grpc, remote_plugin_pb2, remote_plugin_pb2_grpc
import json
from .utils import to_value, from_value, to_struct, from_struct
from ..services.exceptions import ConnpyError

# Import local services
from ..services.node_service import NodeService
from ..services.profile_service import ProfileService
from ..services.config_service import ConfigService
from ..services.plugin_service import PluginService
from ..services.ai_service import AIService
from ..services.system_service import SystemService
from ..services.execution_service import ExecutionService
from ..services.import_export_service import ImportExportService

def handle_errors(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ConnpyError as e:
            context = kwargs.get("context") or args[-1]
            context.abort(grpc.StatusCode.INTERNAL, str(e))
        except Exception as e:
            context = kwargs.get("context") or args[-1]
            context.abort(grpc.StatusCode.UNKNOWN, str(e))
    return wrapper

class NodeServicer(connpy_pb2_grpc.NodeServiceServicer):
    def __init__(self, config):
        self.service = NodeService(config)

    @handle_errors
    def interact_node(self, request_iterator, context):
        import sys
        import select
        import os
        from connpy.core import node
        from ..services.profile_service import ProfileService

        # Fetch first setup packet
        try:
            first_req = next(request_iterator)
        except StopIteration:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "No setup request received")

        unique_id = first_req.id
        sftp = first_req.sftp
        debug = first_req.debug

        node_data = self.service.config.getitem(unique_id, extract=False)
        profile_service = ProfileService(self.service.config)
        resolved_data = profile_service.resolve_node_data(node_data)
        
        n = node(unique_id, **resolved_data, config=self.service.config)
        if sftp:
            n.protocol = "sftp"

        connect = n._connect(debug=debug)
        if connect != True:
            context.abort(grpc.StatusCode.INTERNAL, "Failed to connect to node")

        import threading
        import queue

        stdin_queue = queue.Queue()
        running = True

        def read_requests():
            try:
                for req in request_iterator:
                    if not running:
                        break
                    if req.cols > 0 and req.rows > 0:
                        try:
                            n.child.setwinsize(req.rows, req.cols)
                        except Exception:
                            pass
                    if req.stdin_data:
                        stdin_queue.put(req.stdin_data)
            except grpc.RpcError:
                pass

        t = threading.Thread(target=read_requests, daemon=True)
        t.start()

        # Set initial window size if provided
        if first_req.cols > 0 and first_req.rows > 0:
            try:
                n.child.setwinsize(first_req.rows, first_req.cols)
            except Exception:
                pass

        try:
            while n.child.isalive() and running:
                r, _, _ = select.select([n.child.child_fd], [], [], 0.05)
                if r:
                    try:
                        data = os.read(n.child.child_fd, 4096)
                        if not data:
                            break
                        yield connpy_pb2.InteractResponse(stdout_data=data)
                    except OSError:
                        break
                
                while not stdin_queue.empty():
                    data = stdin_queue.get_nowait()
                    try:
                        os.write(n.child.child_fd, data)
                    except OSError:
                        running = False
                        break
        finally:
            running = False
            try:
                n.child.terminate(force=True)
            except Exception:
                pass

    @handle_errors
    def list_nodes(self, request, context):
        f = request.filter_str if request.filter_str else None
        fmt = request.format_str if request.format_str else None
        return connpy_pb2.ValueResponse(data=to_value(self.service.list_nodes(f, fmt)))

    @handle_errors
    def list_folders(self, request, context):
        f = request.filter_str if request.filter_str else None
        return connpy_pb2.ValueResponse(data=to_value(self.service.list_folders(f)))

    @handle_errors
    def get_node_details(self, request, context):
        return connpy_pb2.StructResponse(data=to_struct(self.service.get_node_details(request.id)))

    @handle_errors
    def explode_unique(self, request, context):
        return connpy_pb2.ValueResponse(data=to_value(self.service.explode_unique(request.id)))

    @handle_errors
    def generate_cache(self, request, context):
        self.service.generate_cache()
        return Empty()

    @handle_errors
    def add_node(self, request, context):
        self.service.add_node(request.id, from_struct(request.data), request.is_folder)
        self.service.generate_cache()
        return Empty()

    @handle_errors
    def update_node(self, request, context):
        self.service.update_node(request.id, from_struct(request.data))
        self.service.generate_cache()
        return Empty()

    @handle_errors
    def delete_node(self, request, context):
        self.service.delete_node(request.id, request.is_folder)
        self.service.generate_cache()
        return Empty()

    @handle_errors
    def move_node(self, request, context):
        self.service.move_node(request.src_id, request.dst_id, request.copy)
        self.service.generate_cache()
        return Empty()

    @handle_errors
    def bulk_add(self, request, context):
        self.service.bulk_add(list(request.ids), list(request.hosts), from_struct(request.common_data))
        self.service.generate_cache()
        return Empty()

    @handle_errors
    def set_reserved_names(self, request, context):
        self.service.set_reserved_names(list(request.items))
        self.service.generate_cache()
        return Empty()

    @handle_errors
    def full_replace(self, request, context):
        connections = from_struct(request.connections)
        profiles = from_struct(request.profiles)
        self.service.full_replace(connections, profiles)
        self.service.generate_cache()
        return Empty()

    @handle_errors
    def get_inventory(self, request, context):
        data = self.service.get_inventory()
        return connpy_pb2.FullReplaceRequest(
            connections=to_struct(data["connections"]),
            profiles=to_struct(data["profiles"])
        )

class ProfileServicer(connpy_pb2_grpc.ProfileServiceServicer):
    def __init__(self, config):
        self.service = ProfileService(config)
        self.node_service = NodeService(config)

    @handle_errors
    def list_profiles(self, request, context):
        f = request.filter_str if request.filter_str else None
        return connpy_pb2.ValueResponse(data=to_value(self.service.list_profiles(f)))

    @handle_errors
    def get_profile(self, request, context):
        return connpy_pb2.StructResponse(data=to_struct(self.service.get_profile(request.name, request.resolve)))

    @handle_errors
    def add_profile(self, request, context):
        self.service.add_profile(request.id, from_struct(request.data))
        self.node_service.generate_cache()
        return Empty()

    @handle_errors
    def resolve_node_data(self, request, context):
        return connpy_pb2.StructResponse(data=to_struct(self.service.resolve_node_data(from_struct(request.data))))

    @handle_errors
    def delete_profile(self, request, context):
        self.service.delete_profile(request.id)
        self.node_service.generate_cache()
        return Empty()

    @handle_errors
    def update_profile(self, request, context):
        self.service.update_profile(request.id, from_struct(request.data))
        self.node_service.generate_cache()
        return Empty()

class ConfigServicer(connpy_pb2_grpc.ConfigServiceServicer):
    def __init__(self, config):
        self.service = ConfigService(config)

    @handle_errors
    def get_settings(self, request, context):
        return connpy_pb2.StructResponse(data=to_struct(self.service.get_settings()))

    @handle_errors
    def get_default_dir(self, request, context):
        return connpy_pb2.StringResponse(value=self.service.get_default_dir())

    @handle_errors
    def set_config_folder(self, request, context):
        self.service.set_config_folder(request.value)
        return Empty()

    @handle_errors
    def update_setting(self, request, context):
        self.service.update_setting(request.key, from_value(request.value))
        return Empty()

    @handle_errors
    def encrypt_password(self, request, context):
        return connpy_pb2.StringResponse(value=self.service.encrypt_password(request.value))

    @handle_errors
    def apply_theme_from_file(self, request, context):
        return connpy_pb2.StructResponse(data=to_struct(self.service.apply_theme_from_file(request.value)))

class PluginServicer(connpy_pb2_grpc.PluginServiceServicer, remote_plugin_pb2_grpc.RemotePluginServiceServicer):
    def __init__(self, config):
        self.service = PluginService(config)

    @handle_errors
    def list_plugins(self, request, context):
        return connpy_pb2.ValueResponse(data=to_value(self.service.list_plugins()))

    @handle_errors
    def add_plugin(self, request, context):
        if request.source_file.startswith("---CONTENT---\n"):
            content = request.source_file[len("---CONTENT---\n"):].encode()
            self.service.add_plugin_from_bytes(request.name, content, request.update)
        else:
            self.service.add_plugin(request.name, request.source_file, request.update)
        return Empty()

    @handle_errors
    def delete_plugin(self, request, context):
        self.service.delete_plugin(request.id)
        return Empty()

    @handle_errors
    def enable_plugin(self, request, context):
        self.service.enable_plugin(request.id)
        return Empty()

    @handle_errors
    def disable_plugin(self, request, context):
        self.service.disable_plugin(request.id)
        return Empty()

    @handle_errors
    def get_plugin_source(self, request, context):
        source = self.service.get_plugin_source(request.id)
        return remote_plugin_pb2.StringResponse(value=source)

    @handle_errors
    def invoke_plugin(self, request, context):
        args_dict = json.loads(request.args_json)
        for chunk in self.service.invoke_plugin(request.name, args_dict):
            yield remote_plugin_pb2.OutputChunk(text=chunk)

class ExecutionServicer(connpy_pb2_grpc.ExecutionServiceServicer):
    def __init__(self, config):
        self.service = ExecutionService(config)

    @handle_errors
    def run_commands(self, request, context):
        import queue
        import threading
        
        nodes_filter = request.nodes[0] if len(request.nodes) == 1 else list(request.nodes)
        
        q = queue.Queue()
        
        def _on_complete(unique, output, status):
            q.put({"unique_id": unique, "output": output, "status": status})
            
        def _worker():
            try:
                self.service.run_commands(
                    nodes_filter=nodes_filter,
                    commands=list(request.commands),
                    folder=request.folder if request.folder else None,
                    prompt=request.prompt if request.prompt else None,
                    parallel=request.parallel,
                    variables=from_struct(request.vars) if request.HasField("vars") else None,
                    on_node_complete=_on_complete
                )
            except Exception as e:
                # Optionally pass error to stream, but handle_errors decorator covers top-level.
                # However, thread exceptions won't reach context.abort directly.
                q.put(e)
            finally:
                q.put(None)
                
        threading.Thread(target=_worker, daemon=True).start()
        
        while True:
            item = q.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
                
            yield connpy_pb2.NodeRunResult(
                unique_id=item["unique_id"],
                output=item["output"],
                status=item["status"]
            )

    @handle_errors
    def test_commands(self, request, context):
        import queue
        import threading
        
        nodes_filter = request.nodes[0] if len(request.nodes) == 1 else list(request.nodes)

        q = queue.Queue()
        
        def _on_complete(unique, output, status, result):
            q.put({"unique_id": unique, "output": output, "status": status, "result": result})
            
        def _worker():
            try:
                self.service.test_commands(
                    nodes_filter=nodes_filter,
                    commands=list(request.commands),
                    expected=request.expected,
                    folder=request.folder if request.folder else None,
                    prompt=request.prompt if request.prompt else None,
                    parallel=request.parallel,
                    variables=from_struct(request.vars) if request.HasField("vars") else None,
                    on_node_complete=_on_complete
                )
            except Exception as e:
                q.put(e)
            finally:
                q.put(None)
                
        threading.Thread(target=_worker, daemon=True).start()
        
        while True:
            item = q.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
                
            res = connpy_pb2.NodeRunResult(
                unique_id=item["unique_id"],
                output=item["output"],
                status=item["status"]
            )
            if item["result"] is not None:
                res.test_result.CopyFrom(to_struct(item["result"]))
            yield res

    @handle_errors
    def run_cli_script(self, request, context):
        res = self.service.run_cli_script(request.param1, request.param2, request.parallel)
        return connpy_pb2.StructResponse(data=to_struct(res))

    @handle_errors
    def run_yaml_playbook(self, request, context):
        res = self.service.run_yaml_playbook(request.param1, request.parallel)
        return connpy_pb2.StructResponse(data=to_struct(res))

class ImportExportServicer(connpy_pb2_grpc.ImportExportServiceServicer):
    def __init__(self, config):
        self.service = ImportExportService(config)
        self.node_service = NodeService(config)

    @handle_errors
    def export_to_file(self, request, context):
        self.service.export_to_file(request.file_path, list(request.folders) if request.folders else None)
        return Empty()

    @handle_errors
    def import_from_file(self, request, context):
        if request.value.startswith("---YAML---\n"):
            import yaml
            content = request.value[len("---YAML---\n"):]
            data = yaml.load(content, Loader=yaml.FullLoader)
            self.service.import_from_dict(data)
        else:
            self.service.import_from_file(request.value)
        self.node_service.generate_cache()
        return Empty()

    @handle_errors
    def set_reserved_names(self, request, context):
        self.service.set_reserved_names(list(request.items))
        self.node_service.generate_cache()
        return Empty()

class StatusBridge:
    def __init__(self, q, request_queue=None):
        self.q = q
        self.request_queue = request_queue
        self.on_interrupt = self._force_interrupt
        self.thread = None

    def _force_interrupt(self):
        """Forcefully raise KeyboardInterrupt in the target thread."""
        if self.thread and self.thread.ident:
            # Standard Python trick to raise an exception in a specific thread
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_long(self.thread.ident), 
                ctypes.py_object(KeyboardInterrupt)
            )

    def update(self, msg):
        self.q.put(("status", msg))
    
    def stop(self):
        pass

    def print(self, *args, **kwargs):
        # Capture Rich output and send as debug message
        self._print_to_queue("debug", *args, **kwargs)

    def print_important(self, *args, **kwargs):
        # Capture Rich output and send as important message (always show)
        self._print_to_queue("important", *args, **kwargs)

    def _print_to_queue(self, msg_type, *args, **kwargs):
        from rich.console import Console
        from io import StringIO
        from ..printer import connpy_theme
        buf = StringIO()
        # Use a high-quality console for rendering with the app's theme
        c = Console(file=buf, force_terminal=True, width=100, theme=connpy_theme)
        c.print(*args, **kwargs)
        self.q.put((msg_type, buf.getvalue()))

    def confirm(self, prompt, default="n"):
        """Bridge confirmation to the gRPC client."""
        if not self.request_queue:
            return default
        
        # Render markup to ANSI for the client
        from rich.console import Console
        from io import StringIO
        from ..printer import connpy_theme
        buf = StringIO()
        c = Console(file=buf, force_terminal=True, theme=connpy_theme)
        c.print(prompt, end="")
        ansi_prompt = buf.getvalue()
        
        # Send confirmation request to client
        self.q.put(("confirm", ansi_prompt))
        
        # Wait for the client to send back the answer via the request stream
        try:
            # Block until we get the next request from the client
            req = self.request_queue.get()
            if req and req.confirmation_answer:
                return req.confirmation_answer
        except Exception:
            pass
        return default

class AIServicer(connpy_pb2_grpc.AIServiceServicer):
    def __init__(self, config):
        self.service = AIService(config)

    @handle_errors
    def ask(self, request_iterator, context):
        import queue
        import threading
        
        # In bidirectional mode, the first request contains the query
        try:
            first_request = next(request_iterator)
        except StopIteration:
            return

        history = from_value(first_request.chat_history)
        
        overrides = {}
        if first_request.engineer_model: overrides["engineer_model"] = first_request.engineer_model
        if first_request.engineer_api_key: overrides["engineer_api_key"] = first_request.engineer_api_key
        if first_request.architect_model: overrides["architect_model"] = first_request.architect_model
        if first_request.architect_api_key: overrides["architect_api_key"] = first_request.architect_api_key

        chunk_queue = queue.Queue()
        request_queue = queue.Queue()
        bridge = StatusBridge(chunk_queue, request_queue=request_queue)
        
        # Start a thread to pull subsequent requests from the client (confirmations)
        def pull_requests():
            try:
                for req in request_iterator:
                    if req.interrupt and bridge.on_interrupt:
                        bridge.on_interrupt()
                    request_queue.put(req)
            except Exception:
                pass
            finally:
                request_queue.put(None)

        threading.Thread(target=pull_requests, daemon=True).start()

        def callback(chunk):
            chunk_queue.put(("text", chunk))

        result_container = {}

        def run_ai():
            try:
                res = self.service.ask(
                    first_request.input_text, 
                    dryrun=first_request.dryrun, 
                    chat_history=history if history else None,
                    session_id=first_request.session_id if first_request.session_id else None,
                    debug=first_request.debug,
                    status=bridge,
                    console=bridge,
                    confirm_handler=bridge.confirm,
                    chunk_callback=callback,
                    trust=first_request.trust,
                    **overrides
                )
                result_container["res"] = res
            except Exception as e:
                chunk_queue.put(("status", f"[bold fail]Error: {str(e)}[/bold fail]"))
                result_container["error"] = e
            finally:
                chunk_queue.put(None) # Sentinel

        t = threading.Thread(target=run_ai, daemon=True)
        bridge.thread = t
        t.start()

        while True:
            item = chunk_queue.get()
            if item is None:
                break
            
            msg_type, val = item
            if msg_type == "text":
                yield connpy_pb2.AIResponse(text_chunk=val, is_final=False)
            elif msg_type == "status":
                yield connpy_pb2.AIResponse(status_update=val, is_final=False)
            elif msg_type == "debug":
                yield connpy_pb2.AIResponse(debug_message=val, is_final=False)
            elif msg_type == "important":
                yield connpy_pb2.AIResponse(important_message=val, is_final=False)
            elif msg_type == "confirm":
                yield connpy_pb2.AIResponse(status_update=val, requires_confirmation=True, is_final=False)

        if "error" in result_container:
            raise result_container["error"]

        yield connpy_pb2.AIResponse(
            is_final=True, 
            full_result=to_struct(result_container.get("res", {}))
        )

    @handle_errors
    def confirm(self, request, context):
        res = self.service.confirm(request.value)
        return connpy_pb2.BoolResponse(value=res)

    @handle_errors
    def list_sessions(self, request, context):
        return connpy_pb2.ValueResponse(data=to_value(self.service.list_sessions()))

    @handle_errors
    def delete_session(self, request, context):
        self.service.delete_session(request.value)
        return Empty()

    @handle_errors
    def configure_provider(self, request, context):
        self.service.configure_provider(request.provider, request.model, request.api_key)
        return Empty()

    @handle_errors
    def load_session_data(self, request, context):
        return connpy_pb2.StructResponse(data=to_struct(self.service.load_session_data(request.value)))

class SystemServicer(connpy_pb2_grpc.SystemServiceServicer):
    def __init__(self, config):
        self.service = SystemService(config)

    @handle_errors
    def start_api(self, request, context):
        self.service.start_api(request.value)
        return Empty()

    @handle_errors
    def debug_api(self, request, context):
        self.service.debug_api(request.value)
        return Empty()

    @handle_errors
    def stop_api(self, request, context):
        self.service.stop_api()
        return Empty()

    @handle_errors
    def restart_api(self, request, context):
        self.service.restart_api(request.value)
        return Empty()

    @handle_errors
    def get_api_status(self, request, context):
        return connpy_pb2.BoolResponse(value=self.service.get_api_status())

class LoggingInterceptor(grpc.ServerInterceptor):
    def __init__(self):
        from rich.console import Console
        from ..printer import connpy_theme
        self.console = Console(theme=connpy_theme)

    def intercept_service(self, continuation, handler_call_details):
        import time
        method = handler_call_details.method
        self.console.print(f"[debug][DEBUG][/debug] gRPC Incoming Request: [bold cyan]{method}[/bold cyan]")
        
        start_time = time.time()
        try:
            result = continuation(handler_call_details)
        except Exception as e:
            self.console.print(f"[debug][DEBUG][/debug] [bold red]ERROR[/bold red] in {method}: {e}")
            raise e
        finally:
            duration = (time.time() - start_time) * 1000
            self.console.print(f"[debug][DEBUG][/debug] Completed [bold cyan]{method}[/bold cyan] in {duration:.2f}ms")
            
        return result

def serve(config, port=8048, debug=False):
    interceptors = [LoggingInterceptor()] if debug else []
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10), interceptors=interceptors)
    
    connpy_pb2_grpc.add_NodeServiceServicer_to_server(NodeServicer(config), server)
    connpy_pb2_grpc.add_ProfileServiceServicer_to_server(ProfileServicer(config), server)
    connpy_pb2_grpc.add_ConfigServiceServicer_to_server(ConfigServicer(config), server)
    plugin_servicer = PluginServicer(config)
    connpy_pb2_grpc.add_PluginServiceServicer_to_server(plugin_servicer, server)
    remote_plugin_pb2_grpc.add_RemotePluginServiceServicer_to_server(plugin_servicer, server)
    connpy_pb2_grpc.add_ExecutionServiceServicer_to_server(ExecutionServicer(config), server)
    connpy_pb2_grpc.add_ImportExportServiceServicer_to_server(ImportExportServicer(config), server)
    connpy_pb2_grpc.add_AIServiceServicer_to_server(AIServicer(config), server)
    connpy_pb2_grpc.add_SystemServiceServicer_to_server(SystemServicer(config), server)
    
    server.add_insecure_port(f'[::]:{port}')
    server.start()
    return server
