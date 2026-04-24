import grpc
import queue
import threading
from functools import wraps
from google.protobuf.empty_pb2 import Empty

from . import connpy_pb2, connpy_pb2_grpc, remote_plugin_pb2, remote_plugin_pb2_grpc
from .utils import to_value, from_value, to_struct, from_struct
from ..services.exceptions import ConnpyError
from ..hooks import MethodHook
from .. import printer

def handle_errors(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except grpc.RpcError as e:
            # Re-raise gRPC errors as native ConnpyError to keep CLI handlers agnostic
            details = e.details()

            # Identify the host if available on the instance
            instance = args[0] if args else None
            host = getattr(instance, "remote_host", "remote host")

            # Make common gRPC errors more readable
            if "failed to connect to all addresses" in details:
                simplified = f"Failed to connect to remote host at {host} (Connection refused)"
            elif "Method not found" in details:
                simplified = f"Remote server at {host} is using an incompatible version"
            elif "Deadline Exceeded" in details:
                simplified = f"Request to {host} timed out"
            else:
                simplified = details

            raise ConnpyError(simplified)
    return wrapper
class NodeStub:
    def __init__(self, channel, remote_host, config=None):
        self.stub = connpy_pb2_grpc.NodeServiceStub(channel)
        self.remote_host = remote_host
        self.config = config

    @handle_errors
    def connect_node(self, unique_id, sftp=False, debug=False, logger=None):
        import sys
        import select
        import tty
        import termios
        import os
        import threading
        
        def request_generator():
            cols, rows = 80, 24
            try:
                size = os.get_terminal_size()
                cols, rows = size.columns, size.lines
            except OSError:
                pass
                
            yield connpy_pb2.InteractRequest(
                id=unique_id, sftp=sftp, debug=debug, cols=cols, rows=rows
            )
            
            while True:
                r, _, _ = select.select([sys.stdin.fileno()], [], [])
                if r:
                    try:
                        data = os.read(sys.stdin.fileno(), 1024)
                        if not data:
                            break
                        yield connpy_pb2.InteractRequest(stdin_data=data)
                    except OSError:
                        break

        # Fetch node details for the connection message
        try:
            node_details = self.get_node_details(unique_id)
            host = node_details.get("host", "unknown")
            port = str(node_details.get("port", ""))
            protocol = "sftp" if sftp else node_details.get("protocol", "ssh")
            port_str = f":{port}" if port and protocol not in ["ssm", "kubectl", "docker"] else ""
            conn_msg = f"Connected to {unique_id} at {host}{port_str} via: {protocol}"
        except Exception:
            conn_msg = f"Connected to {unique_id}"

        old_tty = termios.tcgetattr(sys.stdin)
        try:
            tty.setraw(sys.stdin.fileno())
            response_iterator = self.stub.interact_node(request_generator())
            
            # First response is connection status
            try:
                first_res = next(response_iterator)
                if first_res.success:
                    # Connection established on server, show success message
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)
                    printer.success(conn_msg)
                    tty.setraw(sys.stdin.fileno())
                else:
                    # Connection failed on server
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)
                    printer.error(f"Connection failed: {first_res.error_message}")
                    return
            except StopIteration:
                return
            
            for res in response_iterator:
                if res.stdout_data:
                    os.write(sys.stdout.fileno(), res.stdout_data)
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)

    @handle_errors
    def connect_dynamic(self, connection_params, debug=False):
        import sys
        import select
        import tty
        import termios
        import os
        import json
        
        params_json = json.dumps(connection_params)
        
        def request_generator():
            cols, rows = 80, 24
            try:
                size = os.get_terminal_size()
                cols, rows = size.columns, size.lines
            except OSError:
                pass
                
            yield connpy_pb2.InteractRequest(
                id="dynamic", debug=debug, cols=cols, rows=rows,
                connection_params_json=params_json
            )
            
            while True:
                r, _, _ = select.select([sys.stdin.fileno()], [], [])
                if r:
                    try:
                        data = os.read(sys.stdin.fileno(), 1024)
                        if not data:
                            break
                        yield connpy_pb2.InteractRequest(stdin_data=data)
                    except OSError:
                        break

        # Prepare connection message
        try:
            node_name = connection_params.get("name", "dynamic@remote")
            host = connection_params.get("host", "dynamic")
            port = str(connection_params.get("port", ""))
            protocol = connection_params.get("protocol", "ssh")
            port_str = f":{port}" if port and protocol not in ["ssm", "kubectl", "docker"] else ""
            conn_msg = f"Connected to {node_name} at {host}{port_str} via: {protocol}"
        except Exception:
            node_name = connection_params.get("name", "dynamic@remote") if isinstance(connection_params, dict) else "dynamic@remote"
            conn_msg = f"Connected to {node_name}"

        old_tty = termios.tcgetattr(sys.stdin)
        try:
            tty.setraw(sys.stdin.fileno())
            response_iterator = self.stub.interact_node(request_generator())
            
            # First response is connection status
            try:
                first_res = next(response_iterator)
                if first_res.success:
                    # Connection established on server, show success message
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)
                    printer.success(conn_msg)
                    tty.setraw(sys.stdin.fileno())
                else:
                    # Connection failed on server
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)
                    printer.error(f"Connection failed: {first_res.error_message}")
                    return
            except StopIteration:
                return
                
            for res in response_iterator:
                if res.stdout_data:
                    os.write(sys.stdout.fileno(), res.stdout_data)
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)

    @MethodHook
    @handle_errors
    def list_nodes(self, filter_str=None, format_str=None):
        req = connpy_pb2.FilterRequest(filter_str=filter_str or "", format_str=format_str or "")
        return from_value(self.stub.list_nodes(req).data) or []

    @MethodHook
    @handle_errors
    def list_folders(self, filter_str=None):
        req = connpy_pb2.FilterRequest(filter_str=filter_str or "")
        return from_value(self.stub.list_folders(req).data) or []

    @handle_errors
    def get_node_details(self, unique_id):
        return from_struct(self.stub.get_node_details(connpy_pb2.IdRequest(id=unique_id)).data)

    @handle_errors
    def explode_unique(self, unique_id):
        return from_value(self.stub.explode_unique(connpy_pb2.IdRequest(id=unique_id)).data)

    @handle_errors
    def validate_parent_folder(self, unique_id):
        self.stub.validate_parent_folder(connpy_pb2.IdRequest(id=unique_id))

    @handle_errors
    def generate_cache(self, nodes=None, folders=None, profiles=None):
        # 1. Update remote cache on server
        self.stub.generate_cache(Empty())
        
        # 2. Update local fzf/text cache files
        # If no data provided, we fetch it all from remote to sync local files
        if nodes is None and folders is None and profiles is None:
            nodes = self.list_nodes()
            folders = self.list_folders()
            # We don't have direct access to ProfileStub here, but usually 
            # node cache is what matters for fzf. We'll fetch profiles if we can.
            # For now, let's sync what we have.
            
        if nodes is not None or folders is not None or profiles is not None:
            self.config._generate_nodes_cache(nodes=nodes, folders=folders, profiles=profiles)

    def _trigger_local_cache_sync(self):
        """Helper to fetch remote data and update local fzf cache files after a change."""
        try:
            nodes = self.list_nodes()
            folders = self.list_folders()
            self.generate_cache(nodes=nodes, folders=folders)
        except Exception:
            # Failure to sync cache shouldn't break the main operation's success feedback
            pass

    @handle_errors
    def add_node(self, unique_id, data, is_folder=False):
        req = connpy_pb2.NodeRequest(id=unique_id, data=to_struct(data), is_folder=is_folder)
        self.stub.add_node(req)
        self._trigger_local_cache_sync()

    @handle_errors
    def update_node(self, unique_id, data):
        req = connpy_pb2.NodeRequest(id=unique_id, data=to_struct(data), is_folder=False)
        self.stub.update_node(req)
        self._trigger_local_cache_sync()

    @handle_errors
    def delete_node(self, unique_id, is_folder=False):
        req = connpy_pb2.DeleteRequest(id=unique_id, is_folder=is_folder)
        self.stub.delete_node(req)
        self._trigger_local_cache_sync()

    @handle_errors
    def move_node(self, src_id, dst_id, copy=False):
        req = connpy_pb2.MoveRequest(src_id=src_id, dst_id=dst_id, copy=copy)
        self.stub.move_node(req)
        self._trigger_local_cache_sync()

    @handle_errors
    def bulk_add(self, ids, hosts, common_data):
        req = connpy_pb2.BulkRequest(ids=ids, hosts=hosts, common_data=to_struct(common_data))
        self.stub.bulk_add(req)
        self._trigger_local_cache_sync()

    @handle_errors
    def set_reserved_names(self, names):
        self.stub.set_reserved_names(connpy_pb2.ListRequest(items=names))
        self._trigger_local_cache_sync()

    @handle_errors
    def full_replace(self, connections, profiles):
        req = connpy_pb2.FullReplaceRequest(
            connections=to_struct(connections),
            profiles=to_struct(profiles)
        )
        self.stub.full_replace(req)
        self._trigger_local_cache_sync()

    @handle_errors
    def get_inventory(self):
        resp = self.stub.get_inventory(Empty())
        return {
            "connections": from_struct(resp.connections),
            "profiles": from_struct(resp.profiles)
        }


class ProfileStub:
    def __init__(self, channel, remote_host, node_stub=None):
        self.stub = connpy_pb2_grpc.ProfileServiceStub(channel)
        self.remote_host = remote_host
        self.node_stub = node_stub

    @handle_errors
    def list_profiles(self, filter_str=None):
        req = connpy_pb2.FilterRequest(filter_str=filter_str or "")
        return from_value(self.stub.list_profiles(req).data) or []

    @handle_errors
    def get_profile(self, name, resolve=True):
        req = connpy_pb2.ProfileRequest(name=name, resolve=resolve)
        return from_struct(self.stub.get_profile(req).data)

    @handle_errors
    def add_profile(self, name, data):
        req = connpy_pb2.NodeRequest(id=name, data=to_struct(data))
        self.stub.add_profile(req)
        if self.node_stub:
            self.node_stub._trigger_local_cache_sync()

    @handle_errors
    def resolve_node_data(self, node_data):
        req = connpy_pb2.StructRequest(data=to_struct(node_data))
        return from_struct(self.stub.resolve_node_data(req).data)

    @handle_errors
    def delete_profile(self, name):
        req = connpy_pb2.IdRequest(id=name)
        self.stub.delete_profile(req)
        if self.node_stub:
            self.node_stub._trigger_local_cache_sync()

    @handle_errors
    def update_profile(self, name, data):
        req = connpy_pb2.NodeRequest(id=name, data=to_struct(data))
        self.stub.update_profile(req)
        if self.node_stub:
            self.node_stub._trigger_local_cache_sync()

class ConfigStub:
    def __init__(self, channel, remote_host):
        self.stub = connpy_pb2_grpc.ConfigServiceStub(channel)
        self.remote_host = remote_host

    @handle_errors
    def get_settings(self):
        return from_struct(self.stub.get_settings(Empty()).data)

    @handle_errors
    def update_setting(self, key, value):
        self.stub.update_setting(connpy_pb2.UpdateRequest(key=key, value=to_value(value)))

    @handle_errors
    def get_default_dir(self):
        return self.stub.get_default_dir(Empty()).value

    @handle_errors
    def set_config_folder(self, folder):
        self.stub.set_config_folder(connpy_pb2.StringRequest(value=folder))

    @handle_errors
    def encrypt_password(self, password):
        return self.stub.encrypt_password(connpy_pb2.StringRequest(value=password)).value

class PluginStub:
    def __init__(self, channel, remote_host):
        self.stub = connpy_pb2_grpc.PluginServiceStub(channel)
        self.remote_stub = remote_plugin_pb2_grpc.RemotePluginServiceStub(channel)
        self.remote_host = remote_host

    @handle_errors
    def list_plugins(self):
        return from_value(self.stub.list_plugins(Empty()).data)

    @handle_errors
    def add_plugin(self, name, source_file, update=False):
        # Read the local file content to send it to the server
        with open(source_file, "r") as f:
            content = f.read()
        
        # Use source_file as a marker for "content-inside"
        marker_content = f"---CONTENT---\n{content}"
        req = connpy_pb2.PluginRequest(name=name, source_file=marker_content, update=update)
        self.stub.add_plugin(req)

    @handle_errors
    def delete_plugin(self, name):
        self.stub.delete_plugin(connpy_pb2.IdRequest(id=name))

    @handle_errors
    def enable_plugin(self, name):
        self.stub.enable_plugin(connpy_pb2.IdRequest(id=name))

    @handle_errors
    def disable_plugin(self, name):
        self.stub.disable_plugin(connpy_pb2.IdRequest(id=name))

    @handle_errors
    def get_plugin_source(self, name):
        resp = self.remote_stub.get_plugin_source(remote_plugin_pb2.IdRequest(id=name))
        return resp.value

    @handle_errors
    def invoke_plugin(self, name, args_namespace):
        import json
        args_dict = {k: v for k, v in vars(args_namespace).items()
                     if isinstance(v, (str, int, float, bool, list, type(None)))}
        if hasattr(args_namespace, "func") and hasattr(args_namespace.func, "__name__"):
            args_dict["__func_name__"] = args_namespace.func.__name__
            
        req = remote_plugin_pb2.PluginInvokeRequest(name=name, args_json=json.dumps(args_dict))
        for chunk in self.remote_stub.invoke_plugin(req):
            yield chunk.text

class ExecutionStub:
    def __init__(self, channel, remote_host):
        self.stub = connpy_pb2_grpc.ExecutionServiceStub(channel)
        self.remote_host = remote_host

    @handle_errors
    def run_commands(self, nodes_filter, commands, variables=None, parallel=10, timeout=10, folder=None, prompt=None, **kwargs):
        nodes_list = [nodes_filter] if isinstance(nodes_filter, str) else list(nodes_filter)
        req = connpy_pb2.RunRequest(
            nodes=nodes_list,
            commands=commands,
            folder=folder or "",
            prompt=prompt or "",
            parallel=parallel,
        )
        # Note: 'timeout', 'on_node_complete', and 'logger' are currently not 
        # sent over gRPC in the current proto definition. 
        if variables is not None:
            req.vars.CopyFrom(to_struct(variables))
            
        final_results = {}
        on_complete = kwargs.get("on_node_complete")
        
        for response in self.stub.run_commands(req):
            if on_complete:
                on_complete(response.unique_id, response.output, response.status)
            final_results[response.unique_id] = response.output
                
        return final_results

    @handle_errors
    def test_commands(self, nodes_filter, commands, expected, variables=None, parallel=10, timeout=10, prompt=None, **kwargs):
        nodes_list = [nodes_filter] if isinstance(nodes_filter, str) else list(nodes_filter)
        req = connpy_pb2.TestRequest(
            nodes=nodes_list,
            commands=commands,
            expected=expected,
            folder=kwargs.get("folder", ""),
            prompt=prompt or "",
            parallel=parallel,
        )
        if variables is not None:
            req.vars.CopyFrom(to_struct(variables))
            
        final_results = {}
        on_complete = kwargs.get("on_node_complete")
        
        for response in self.stub.test_commands(req):
            result_dict = from_struct(response.test_result) if response.HasField("test_result") else {}
            if on_complete:
                on_complete(response.unique_id, response.output, response.status, result_dict)
            final_results[response.unique_id] = result_dict
                
        return final_results

    @handle_errors
    def run_cli_script(self, nodes_filter, script_path, parallel=10):
        req = connpy_pb2.ScriptRequest(param1=nodes_filter, param2=script_path, parallel=parallel)
        return from_struct(self.stub.run_cli_script(req).data)

    @handle_errors
    def run_yaml_playbook(self, playbook_path, parallel=10):
        req = connpy_pb2.ScriptRequest(param1=playbook_path, parallel=parallel)
        return from_struct(self.stub.run_yaml_playbook(req).data)

class ImportExportStub:
    def __init__(self, channel, remote_host):
        self.stub = connpy_pb2_grpc.ImportExportServiceStub(channel)
        self.remote_host = remote_host

    @handle_errors
    def export_to_file(self, file_path, folders=None):
        req = connpy_pb2.ExportRequest(file_path=file_path, folders=folders or [])
        self.stub.export_to_file(req)

    @handle_errors
    def import_from_file(self, file_path):
        with open(file_path, "r") as f:
            content = f.read()
        # Marker to tell the server this is content, not a path
        marker_content = f"---YAML---\n{content}"
        self.stub.import_from_file(connpy_pb2.StringRequest(value=marker_content))

    @handle_errors
    def set_reserved_names(self, names):
        self.stub.set_reserved_names(connpy_pb2.ListRequest(items=names))

class AIStub:
    def __init__(self, channel, remote_host):
        self.stub = connpy_pb2_grpc.AIServiceStub(channel)
        self.remote_host = remote_host

    @handle_errors
    def ask(self, input_text, dryrun=False, chat_history=None, session_id=None, debug=False, status=None, **overrides):
        import queue
        from rich.prompt import Prompt
        from rich.text import Text
        from rich.live import Live
        from rich.panel import Panel
        from rich.markdown import Markdown
        
        req_queue = queue.Queue()
        
        initial_req = connpy_pb2.AskRequest(
            input_text=input_text,
            dryrun=dryrun,
            session_id=session_id or "",
            debug=debug,
            engineer_model=overrides.get("engineer_model", ""),
            engineer_api_key=overrides.get("engineer_api_key", ""),
            architect_model=overrides.get("architect_model", ""),
            architect_api_key=overrides.get("architect_api_key", ""),
            trust=overrides.get("trust", False)
        )
        if chat_history is not None:
            initial_req.chat_history.CopyFrom(to_value(chat_history))
            
        req_queue.put(initial_req)

        def request_generator():
            while True:
                req = req_queue.get()
                if req is None: break
                yield req

        responses = self.stub.ask(request_generator())
        
        full_content = ""
        live_display = None
        final_result = {"response": "", "chat_history": []}

        # Background thread to pull responses from gRPC into a local queue
        # This prevents KeyboardInterrupt from corrupting the gRPC iterator state
        response_queue = queue.Queue()
        
        def pull_responses():
            try:
                for response in responses:
                    response_queue.put(("data", response))
            except Exception as e:
                response_queue.put(("error", e))
            finally:
                response_queue.put((None, None))

        threading.Thread(target=pull_responses, daemon=True).start()

        try:
            while True:
                try:
                    # BLOCKING GET from local queue (interruptible by signal)
                    msg_type, response = response_queue.get()
                except KeyboardInterrupt:
                    # Signal interruption to the server
                    if status:
                        status.update("[error]Interrupted! Closing pending tasks...")
                    
                    # Send the interrupt signal to the server
                    req_queue.put(connpy_pb2.AskRequest(interrupt=True))
                    
                    # CONTINUE the loop to receive remaining data and summary from the queue
                    continue
                
                if msg_type is None: # Sentinel
                    break
                
                if msg_type == "error":
                    # Re-raise or handle gRPC error from background thread
                    if isinstance(response, grpc.RpcError):
                        raise response
                    printer.warning(f"Stream interrupted: {response}")
                    break

                if response.status_update:
                    if response.requires_confirmation:
                        if status: status.stop()
                        if live_display: live_display.stop()
                        
                        # Show prompt and wait for answer
                        prompt_text = Text.from_ansi(response.status_update)
                        ans = Prompt.ask(prompt_text)
                        
                        if status: 
                            status.update("[ai_status]Agent: Resuming...")
                            status.start()
                        if live_display: live_display.start()
                        
                        req_queue.put(connpy_pb2.AskRequest(confirmation_answer=ans))
                        continue
                        
                    if status:
                        status.update(response.status_update)
                    continue
                
                if response.debug_message:
                    if debug:
                        printer.console.print(Text.from_ansi(response.debug_message))
                    continue
                
                if response.important_message:
                    printer.console.print(Text.from_ansi(response.important_message))
                    continue

                if not response.is_final:
                    full_content += response.text_chunk
                    
                    if not live_display and not debug:
                        if status: status.stop()
                        live_display = Live(
                            Panel(Markdown(full_content), title="AI Assistant", expand=False),
                            console=printer.console,
                            refresh_per_second=8,
                            transient=False
                        )
                        live_display.start()
                    elif live_display:
                        live_display.update(Panel(Markdown(full_content), title="AI Assistant", expand=False))
                    continue
                
                if response.is_final:
                    final_result = from_struct(response.full_result)
                    responder = final_result.get("responder", "engineer")
                    alias = "architect" if responder == "architect" else "engineer"
                    role_label = "Network Architect" if responder == "architect" else "Network Engineer"
                    title = f"[bold {alias}]{role_label}[/bold {alias}]"
                    
                    if live_display:
                        live_display.update(Panel(Markdown(full_content), title=title, border_style=alias, expand=False))
                        live_display.stop()
                    elif full_content:
                        printer.console.print(Panel(Markdown(full_content), title=title, border_style=alias, expand=False))
                    break
        except Exception as e:
            # Check if it was a gRPC error that we should let handle_errors catch
            if isinstance(e, grpc.RpcError):
                raise
            printer.warning(f"Stream interrupted: {e}")
        finally:
            req_queue.put(None)
        
        if full_content:
            final_result["streamed"] = True
            
        return final_result

    @handle_errors
    def confirm(self, input_text, console=None):
        return self.stub.confirm(connpy_pb2.StringRequest(value=input_text)).value

    @handle_errors
    def list_sessions(self):
        return from_value(self.stub.list_sessions(Empty()).data)

    @handle_errors
    def delete_session(self, session_id):
        self.stub.delete_session(connpy_pb2.StringRequest(value=session_id))

    @handle_errors
    def configure_provider(self, provider, model=None, api_key=None):
        req = connpy_pb2.ProviderRequest(provider=provider, model=model or "", api_key=api_key or "")
        self.stub.configure_provider(req)

    @handle_errors
    def load_session_data(self, session_id):
        return from_struct(self.stub.load_session_data(connpy_pb2.StringRequest(value=session_id)).data)

class SystemStub:
    def __init__(self, channel, remote_host):
        self.stub = connpy_pb2_grpc.SystemServiceStub(channel)
        self.remote_host = remote_host

    @handle_errors
    def start_api(self, port=None):
        self.stub.start_api(connpy_pb2.IntRequest(value=port or 8048))

    @handle_errors
    def debug_api(self, port=None):
        self.stub.debug_api(connpy_pb2.IntRequest(value=port or 8048))

    @handle_errors
    def stop_api(self):
        self.stub.stop_api(Empty())

    @handle_errors
    def restart_api(self, port=None):
        self.stub.restart_api(connpy_pb2.IntRequest(value=port or 8048))

    @handle_errors
    def get_api_status(self):
        return self.stub.get_api_status(Empty()).value
