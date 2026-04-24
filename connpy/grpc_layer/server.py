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
from .. import printer

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
    import inspect
    if inspect.isgeneratorfunction(func):
        def wrapper(*args, **kwargs):
            try:
                for item in func(*args, **kwargs):
                    yield item
            except ConnpyError as e:
                context = kwargs.get("context") or args[-1]
                context.abort(grpc.StatusCode.INTERNAL, str(e))
            except Exception as e:
                context = kwargs.get("context") or args[-1]
                context.abort(grpc.StatusCode.UNKNOWN, str(e))
            finally:
                printer.clear_thread_state()
        return wrapper
    else:
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except ConnpyError as e:
                context = kwargs.get("context") or args[-1]
                context.abort(grpc.StatusCode.INTERNAL, str(e))
            except Exception as e:
                context = kwargs.get("context") or args[-1]
                context.abort(grpc.StatusCode.UNKNOWN, str(e))
            finally:
                printer.clear_thread_state()
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
        printer.console.print(f"[debug][DEBUG][/debug] gRPC interact_node request for: [bold cyan]{unique_id}[/bold cyan]")

        if first_req.connection_params_json:
            import json
            params = json.loads(first_req.connection_params_json)
            base_node_id = params.get("base_node")
            # Valid attributes that a node object accepts
            valid_attrs = ['host', 'options', 'logs', 'password', 'port', 'protocol', 'user', 'jumphost']
            
            fallback_id = f"{unique_id}@remote"
            if unique_id == "dynamic" and params.get("host"):
                fallback_id = f"dynamic-{params.get('host')}@remote"
            
            if base_node_id:
                # Look up the base node in config and use its full data
                nodes = self.service.config._getallnodes(base_node_id)
                if nodes:
                    device = self.service.config.getitem(nodes[0])
                    # Override device properties with any passed in params
                    for attr in valid_attrs:
                        if attr in params:
                            device[attr] = params[attr]
                    
                    if "tags" in params:
                        device_tags = device.get("tags", {})
                        if not isinstance(device_tags, dict):
                            device_tags = {}
                        device_tags.update(params["tags"])
                        device["tags"] = device_tags
                        
                    node_name = params.get("name", base_node_id)
                    n = node(node_name, **device, config=self.service.config)
                else:
                    # base_node not found, fall back to dynamic
                    node_name = params.get("name", fallback_id)
                    n = node(node_name, host=params.get("host", ""), config=self.service.config)
                    for attr in valid_attrs:
                        if attr in params:
                            setattr(n, attr, params[attr])
                    if "tags" in params:
                        n.tags = params["tags"]
            else:
                node_name = params.get("name", fallback_id)
                n = node(node_name, host=params.get("host", ""), config=self.service.config)
                for attr in valid_attrs:
                    if attr in params:
                        setattr(n, attr, params[attr])
                if "tags" in params:
                    n.tags = params["tags"]
        else:
            node_data = self.service.config.getitem(unique_id, extract=False)
            if not node_data:
                context.abort(grpc.StatusCode.NOT_FOUND, f"Node {unique_id} not found")
            profile_service = ProfileService(self.service.config)
            resolved_data = profile_service.resolve_node_data(node_data)
            n = node(unique_id, **resolved_data, config=self.service.config)
            if sftp:
                n.protocol = "sftp"

        connect = n._connect(debug=debug)
        if connect != True:
            yield connpy_pb2.InteractResponse(success=False, error_message=str(connect))
            return
        
        # Signal successful connection to the client
        yield connpy_pb2.InteractResponse(success=True)

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
    def validate_parent_folder(self, request, context):
        self.service.validate_parent_folder(request.id)
        return Empty()

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
    def __init__(self, q, request_queue=None, is_web=False):
        self.q = q
        self.request_queue = request_queue
        self.on_interrupt = self._force_interrupt
        self.thread = None
        self.is_web = is_web

    def _force_interrupt(self):
        """Forcefully raise KeyboardInterrupt in the target thread."""
        if self.thread and self.thread.ident:
            # Standard Python trick to raise an exception in a specific thread
            import ctypes
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
        from rich.panel import Panel
        from io import StringIO
        from ..printer import connpy_theme
        
        processed_args = list(args)
        if self.is_web:
            # Remove Panels to avoid box characters on web, but preserve Title
            processed_args = []
            for arg in args:
                if isinstance(arg, Panel):
                    # If it has a title, prepend it to the content to allow detection
                    content = arg.renderable
                    if arg.title:
                        processed_args.append(f"{arg.title}\n")
                    processed_args.append(content)
                else:
                    processed_args.append(arg)

        buf = StringIO()
        # force_terminal=False removes ANSI escape codes for Web
        c = Console(file=buf, force_terminal=not self.is_web, width=100, theme=connpy_theme)
        c.print(*processed_args, **kwargs)
        
        text_content = buf.getvalue().strip()
        if text_content:
            self.q.put((msg_type, text_content))

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

        chunk_queue = queue.Queue()
        request_queue = queue.Queue()
        bridge = None
        history = []
        is_web = False
        
        # Dedicated event to signal AI thread to stop
        ai_thread = None
        agent_instance = None

        def callback(chunk):
            chunk_queue.put(("text", chunk))

        def run_ai_task(input_text, session_id, debug, overrides, trust):
            nonlocal history, bridge, agent_instance
            try:
                # Run the AI interaction (this blocks this specific thread)
                res = self.service.ask(
                    input_text,
                    chat_history=history if history else None,
                    session_id=session_id,
                    debug=debug,
                    status=bridge,
                    console=bridge,
                    confirm_handler=bridge.confirm,
                    chunk_callback=callback,
                    trust=trust,
                    **overrides
                )

                # Update history for next message
                if "chat_history" in res:
                    history = res["chat_history"]

                # Send final chunk marker
                chunk_queue.put(("final_mark", res))
            except Exception as e:
                import traceback
                print(f"AI Task Error: {e}")
                traceback.print_exc()
                chunk_queue.put(("status", f"Error: {str(e)}"))

        def request_listener():
            nonlocal bridge, is_web, ai_thread, agent_instance
            try:
                for req in request_iterator:
                    if req.interrupt:
                        if bridge and bridge.on_interrupt:
                            bridge.on_interrupt()
                        continue

                    if req.confirmation_answer:
                        request_queue.put(req)
                        continue

                    if req.input_text:
                        is_web = "web" in (req.session_id or "").lower() or (req.session_id or "").lower().startswith("ws-")
                        if not bridge:
                            bridge = StatusBridge(chunk_queue, request_queue=request_queue, is_web=is_web)

                        overrides = {}
                        if req.engineer_model: overrides["engineer_model"] = req.engineer_model
                        if req.engineer_api_key: overrides["engineer_api_key"] = req.engineer_api_key
                        
                        # Start AI in its own thread so we can keep listening for interrupts
                        ai_thread = threading.Thread(
                            target=run_ai_task,
                            args=(req.input_text, req.session_id, req.debug, overrides, req.trust),
                            daemon=True
                        )
                        ai_thread.start()
            except Exception as e:
                print(f"Request Listener Error: {e}")
            finally:
                # When client closes stream, send sentinel
                chunk_queue.put((None, None))

        # Start listening for client requests/signals
        threading.Thread(target=request_listener, daemon=True).start()

        # Main response loop (yields to gRPC)
        while True:
            item = chunk_queue.get()
            if item == (None, None):
                break

            msg_type, val = item
            if msg_type == "text":
                yield connpy_pb2.AIResponse(text_chunk=val, is_final=False)
            elif msg_type == "status":
                if is_web and "is thinking" in val.lower(): continue
                clean_val = val.replace("[ai_status]", "").replace("[/ai_status]", "")
                yield connpy_pb2.AIResponse(status_update=clean_val, is_final=False)
            elif msg_type == "debug":
                yield connpy_pb2.AIResponse(debug_message=val, is_final=False)
            elif msg_type == "important":
                yield connpy_pb2.AIResponse(important_message=val, is_final=False)
            elif msg_type == "confirm":
                yield connpy_pb2.AIResponse(status_update=val, requires_confirmation=True, is_final=False)
            elif msg_type == "final_mark":
                yield connpy_pb2.AIResponse(is_final=True, full_result=to_struct(val))

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
        from ..printer import connpy_theme, get_original_stdout
        self.console = Console(theme=connpy_theme, file=get_original_stdout())

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
