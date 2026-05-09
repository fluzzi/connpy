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
        import queue
        import os
        import threading
        
        request_queue = queue.Queue()
        client_buffer_bytes = bytearray()
        cmd_byte_positions = [(0, None)]
        pause_stdin = [False]
        wake_r, wake_w = os.pipe()

        def pause_generator():
            pause_stdin[0] = True
            os.write(wake_w, b'\x00')

        def resume_generator():
            pause_stdin[0] = False

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
                try:
                    while True:
                        req = request_queue.get_nowait()
                        if req is None:
                            return
                        yield req
                except queue.Empty:
                    pass

                if pause_stdin[0]:
                    import time
                    time.sleep(0.05)
                    continue

                r, _, _ = select.select([sys.stdin.fileno(), wake_r], [], [], 0.05)
                if wake_r in r:
                    os.read(wake_r, 1)
                    continue
                if sys.stdin.fileno() in r and not pause_stdin[0]:
                    try:
                        data = os.read(sys.stdin.fileno(), 1024)
                        if not data:
                            break
                        if b'\r' in data or b'\n' in data:
                            cmd_byte_positions.append((len(client_buffer_bytes), None))
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
            import time
            tty.setraw(sys.stdin.fileno())
            response_iterator = self.stub.interact_node(request_generator())
            
            import queue
            response_queue = queue.Queue()
            
            def response_consumer():
                try:
                    for r in response_iterator:
                        response_queue.put(r)
                except Exception:
                    pass
                response_queue.put(None)
                
            t_consumer = threading.Thread(target=response_consumer, daemon=True)
            t_consumer.start()
            
            # First phase: Wait for connection status, print early data
            try:
                while True:
                    res = response_queue.get()
                    if res is None:
                        return
                    if res.stdout_data:
                        data = res.stdout_data
                        if debug:
                            data = data.replace(b'\x1b[H\x1b[2J', b'').replace(b'\x1bc', b'').replace(b'\x1b[3J', b'')
                        os.write(sys.stdout.fileno(), data)
                    
                    if res.success:
                        # Connection established on server, show success message
                        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)
                        printer.success(conn_msg)
                        pause_stdin[0] = False
                        tty.setraw(sys.stdin.fileno())
                        break
                    
                    if res.error_message:
                        # Connection failed on server
                        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)
                        printer.error(f"Connection failed: {res.error_message}")
                        return
            except queue.Empty:
                return
            
            # Second phase: Stream active session
            # Clear screen filter is only applied before success (Phase 1).
            # Once the user has a prompt, Ctrl+L must work normally.
            while True:
                res = response_queue.get()
                if res is None:
                    break
                if res.copilot_prompt:
                    pause_generator()
                    import json
                    import asyncio
                    import re
                    from rich.console import Console
                    from rich.panel import Panel
                    from rich.markdown import Markdown
                    from prompt_toolkit import PromptSession
                    from prompt_toolkit.key_binding import KeyBindings
                    from prompt_toolkit.formatted_text import HTML
                    from prompt_toolkit.history import InMemoryHistory
                    from ..printer import connpy_theme
                    from ..core import copilot_terminal_mode
                    
                    if not hasattr(self, 'copilot_history'):
                        self.copilot_history = InMemoryHistory()
                    
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)
                    import fcntl
                    flags = fcntl.fcntl(sys.stdin.fileno(), fcntl.F_GETFL)
                    fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
                    console = Console(theme=connpy_theme)
                    console.print("\n")
                    console.print(Panel(
                        "[bold cyan]AI Terminal Copilot[/bold cyan]\n"
                        "[dim]Type your question. Enter to send, Escape/Ctrl+C to cancel.\n"
                        "Tab to change context mode. Ctrl+\u2191/\u2193 to adjust context. \u2191\u2193 for question history.[/dim]",
                        border_style="cyan"
                    ))
                    
                    node_info = json.loads(res.copilot_node_info_json) if res.copilot_node_info_json else {}
                    
                    # Logic for context selection
                    blocks = []
                    raw_bytes = client_buffer_bytes
                    from ..core import node
                    dummy_node = node("dummy", "dummy") # For logclean
                    
                    if cmd_byte_positions and len(cmd_byte_positions) >= 2 and raw_bytes:
                        default_prompt = r'>$|#$|\$$|>.$|#.$|\$.$'
                        device_prompt = node_info.get("prompt", default_prompt)
                        prompt_re_str = re.sub(r'(?<!\\)\$', '', device_prompt)
                        try:
                            prompt_re = re.compile(prompt_re_str)
                        except Exception:
                            prompt_re = re.compile(re.sub(r'(?<!\\)\$', '', default_prompt))
                            
                        for i in range(1, len(cmd_byte_positions)):
                            pos, known_cmd = cmd_byte_positions[i]
                            prev_pos = cmd_byte_positions[i-1][0]
                            
                            if known_cmd:
                                # AI-injected command: we already know the command text
                                prev_chunk = raw_bytes[prev_pos:pos]
                                prev_cleaned = dummy_node._logclean(prev_chunk.decode(errors='replace'), var=True)
                                prev_lines = [l for l in prev_cleaned.split('\n') if l.strip()]
                                prompt_text = prev_lines[-1].strip() if prev_lines else ""
                                preview = f"{prompt_text}{known_cmd}" if prompt_text else known_cmd
                                blocks.append((pos, preview[:80]))
                            else:
                                # User-typed command: derive from raw log chunk
                                chunk = raw_bytes[prev_pos:pos]
                                cleaned = dummy_node._logclean(chunk.decode(errors='replace'), var=True)
                                lines = [l for l in cleaned.split('\n') if l.strip()]
                                preview = lines[-1].strip() if lines else ""
                                
                                if preview:
                                    match = prompt_re.search(preview)
                                    if match:
                                        cmd_text = preview[match.end():].strip()
                                        if cmd_text:
                                            blocks.append((pos, preview[:80]))
                        
                    clean_buffer = dummy_node._logclean(raw_bytes.decode(errors='replace'), var=True)
                    last_line = clean_buffer.split('\n')[-1].strip() if clean_buffer.strip() else "(prompt)"
                    blocks.append((len(raw_bytes), last_line[:80]))
                            
                    context_cmd = [1]
                    total_cmds = len(blocks)
                    total_lines = len(clean_buffer.split('\n'))
                    context_lines = [min(50, total_lines)]
                    context_mode = [0]
                    MODE_RANGE, MODE_SINGLE, MODE_LINES = 0, 1, 2
                    
                    bindings = KeyBindings()
                    
                    @bindings.add('c-up')
                    def _(event):
                        if context_mode[0] == MODE_LINES:
                            if context_lines[0] >= total_lines:
                                context_lines[0] = min(50, total_lines)
                            else:
                                context_lines[0] = min(context_lines[0] + 50, total_lines)
                        else:
                            if context_cmd[0] < total_cmds:
                                context_cmd[0] += 1
                            else:
                                context_cmd[0] = 1
                        event.app.invalidate()
                    
                    @bindings.add('c-down')
                    def _(event):
                        if context_mode[0] == MODE_LINES:
                            if context_lines[0] <= min(50, total_lines):
                                context_lines[0] = total_lines
                            else:
                                context_lines[0] = max(context_lines[0] - 50, min(50, total_lines))
                        else:
                            if context_cmd[0] > 1:
                                context_cmd[0] -= 1
                            else:
                                context_cmd[0] = total_cmds
                        event.app.invalidate()
                    
                    @bindings.add('tab')
                    def _(event):
                        context_mode[0] = (context_mode[0] + 1) % 3
                        event.app.invalidate()
                        
                    @bindings.add('escape')
                    def _(event):
                        event.app.exit(result='')
                        
                    def get_current_block():
                        idx = max(0, total_cmds - context_cmd[0])
                        return idx, blocks[idx]
                    
                    def get_active_buffer():
                        if context_mode[0] == MODE_LINES:
                            buffer_lines = clean_buffer.split('\n')
                            return '\n'.join(buffer_lines[-context_lines[0]:])
                        
                        idx, (start, preview) = get_current_block()
                        if context_mode[0] == MODE_SINGLE and idx + 1 < total_cmds:
                            end = blocks[idx + 1][0]
                            active_raw = raw_bytes[start:end]
                        else:
                            active_raw = raw_bytes[start:]
                        return preview + "\n" + dummy_node._logclean(active_raw.decode(errors='replace'), var=True)
                        
                    def get_prompt_text():
                        if context_mode[0] == MODE_LINES:
                            return HTML(f"<ansicyan>Ask [Ctx: {context_lines[0]}/{total_lines}L]: </ansicyan>")
                        
                        lines_count = len(get_active_buffer().split('\n'))
                        if context_mode[0] == MODE_SINGLE:
                            return HTML(f"<ansicyan>Ask [Cmd {context_cmd[0]} ~{lines_count}L]: </ansicyan>")
                        else:
                            return HTML(f"<ansicyan>Ask [Cmd {context_cmd[0]}\u2192END ~{lines_count}L]: </ansicyan>")
                        
                    def get_toolbar():
                        mode_labels = {MODE_RANGE: "RANGE", MODE_SINGLE: "SINGLE", MODE_LINES: "LINES"}
                        mode_label = mode_labels[context_mode[0]]
                        if context_mode[0] == MODE_LINES:
                            return HTML(f"<ansigray>\u25b6 Ctrl+\u2191/\u2193 adjusts by 50 lines  [Tab: {mode_label}]</ansigray>")
                        _, (_, preview) = get_current_block()
                        return HTML(f"<ansigray>\u25b6 {preview}  [Tab: {mode_label}]</ansigray>")
                    
                    try:
                        session = PromptSession(history=self.copilot_history)
                        question = session.prompt(get_prompt_text, key_bindings=bindings, bottom_toolbar=get_toolbar)
                    except KeyboardInterrupt:
                        question = ""

                    if not question or not question.strip() or question.strip() == "CANCEL":
                        console.print("\n[dim]Copilot cancelled.[/dim]")
                        request_queue.put(connpy_pb2.InteractRequest(copilot_question="CANCEL"))
                        resume_generator()
                        tty.setraw(sys.stdin.fileno())
                        continue

                    active_buffer = get_active_buffer()
                    request_queue.put(connpy_pb2.InteractRequest(copilot_question=question, copilot_context_buffer=active_buffer))
                    
                    from rich.live import Live
                    live_text = "Thinking..."
                    panel = Panel(live_text, title="[bold cyan]Copilot Guide[/bold cyan]", border_style="cyan")
                    result = {}
                    cancelled = False
                    
                    with copilot_terminal_mode(), Live(panel, console=console, refresh_per_second=10) as live:
                        # Make stdin non-blocking to check for Ctrl+C locally
                        import fcntl
                        flags = fcntl.fcntl(sys.stdin.fileno(), fcntl.F_GETFL)
                        fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, flags | os.O_NONBLOCK)
                        
                        while True:
                            # 1. Read input for Ctrl+C
                            try:
                                key = os.read(sys.stdin.fileno(), 1024)
                                if b'\x03' in key:
                                    cancelled = True
                                    request_queue.put(connpy_pb2.InteractRequest(copilot_question="CANCEL"))
                                    console.print("\n[dim]Copilot cancelled via Ctrl+C. Disconnecting...[/dim]")
                                    break
                            except OSError:
                                pass
                                
                            # 2. Wait for response chunk
                            try:
                                chunk_res = response_queue.get(timeout=0.1)
                                if chunk_res is None:
                                    break
                                    
                                if chunk_res.copilot_stream_chunk:
                                    if live_text == "Thinking...": live_text = ""
                                    live_text += chunk_res.copilot_stream_chunk
                                    live.update(Panel(Markdown(live_text), title="[bold cyan]Copilot Guide[/bold cyan]", border_style="cyan"))
                                elif chunk_res.copilot_response_json:
                                    result = json.loads(chunk_res.copilot_response_json)
                                    break
                            except queue.Empty:
                                continue
                                
                        # Restore blocking mode
                        fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, flags)

                    if cancelled:
                        resume_generator()
                        tty.setraw(sys.stdin.fileno())
                        continue
                        
                    if result.get("error"):
                        console.print(f"[red]Error: {result['error']}[/red]")
                        request_queue.put(connpy_pb2.InteractRequest(copilot_action="cancel"))
                        resume_generator()
                        tty.setraw(sys.stdin.fileno())
                        continue

                    if live_text == "Thinking..." and result.get("guide"):
                        console.print(Panel(Markdown(result["guide"]), title="[bold cyan]Copilot Guide[/bold cyan]", border_style="cyan"))

                    commands = result.get("commands", [])
                    risk = result.get("risk_level", "low")
                    risk_style = {"low": "green", "high": "yellow", "destructive": "red"}.get(risk, "green")
                    
                    action_sent = "cancel"
                    if commands:
                        cmd_text = "\n".join(f"  {i+1}. {cmd}" for i, cmd in enumerate(commands))
                        console.print(Panel(
                            cmd_text,
                            title=f"[bold {risk_style}]Suggested Commands [{risk.upper()}][/bold {risk_style}]",
                            border_style=risk_style
                        ))
                        
                        try:
                            confirm_session = PromptSession()
                            confirm_bindings = KeyBindings()
                            @confirm_bindings.add('escape')
                            def _(event):
                                event.app.exit(result='n')
                            
                            pt_color = "ansi" + risk_style
                            action = confirm_session.prompt(
                                HTML(f"<{pt_color}>Send commands? (y/n/e/number/range) [n]: </{pt_color}>"),
                                key_bindings=confirm_bindings
                            )
                        except KeyboardInterrupt:
                            action = "n"
                            
                        if not action.strip():
                            action = "n"
                            
                        action_l = action.lower().strip()
                        if action_l in ('y', 'yes', 'all'):
                            action_sent = "send_all"
                        elif action_l.startswith('e'):
                            action_sent = f"edit_{action_l[1:]}" if len(action_l) > 1 else "edit_all"
                            # For remote editing, the client edits and sends back as custom action 
                            edit_session = PromptSession()
                            cmds_to_edit = []
                            if action_sent.startswith("edit_") and action_sent[5:].isdigit():
                                idx = int(action_sent[5:]) - 1
                                if 0 <= idx < len(commands):
                                    cmds_to_edit = [commands[idx]]
                            else:
                                cmds_to_edit = commands
                                
                            if cmds_to_edit:
                                target_cmd = "\n".join(cmds_to_edit)
                                try:
                                    edited_cmd = edit_session.prompt(
                                        HTML("<ansicyan>Edit commands (Alt+Enter or Esc,Enter to submit):\n</ansicyan>"),
                                        default=target_cmd,
                                        multiline=True
                                    )
                                    if edited_cmd.strip():
                                        action_sent = "custom:" + edited_cmd.strip()
                                    else:
                                        action_sent = "cancel"
                                except KeyboardInterrupt:
                                    action_sent = "cancel"
                        elif action_l not in ('n', 'no', ''):
                            action_sent = action_l
                    
                    console.print("[dim]Returning to session...[/dim]\n")
                    request_queue.put(connpy_pb2.InteractRequest(copilot_action=action_sent))
                    resume_generator()
                    tty.setraw(sys.stdin.fileno())
                    continue

                if res.copilot_injected_command:
                    cmd_byte_positions.append((len(client_buffer_bytes), res.copilot_injected_command))

                if res.stdout_data:
                    os.write(sys.stdout.fileno(), res.stdout_data)
                    client_buffer_bytes.extend(res.stdout_data)
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)
            os.close(wake_r)
            os.close(wake_w)

    @handle_errors
    def connect_dynamic(self, connection_params, debug=False):
        import sys
        import select
        import tty
        import termios
        import queue
        import os
        import json
        
        params_json = json.dumps(connection_params)
        request_queue = queue.Queue()
        client_buffer_bytes = bytearray()
        cmd_byte_positions = [(0, None)]
        pause_stdin = [False]
        wake_r, wake_w = os.pipe()

        def pause_generator():
            pause_stdin[0] = True
            os.write(wake_w, b'\x00')

        def resume_generator():
            pause_stdin[0] = False
        
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
                try:
                    while True:
                        req = request_queue.get_nowait()
                        if req is None:
                            return
                        yield req
                except queue.Empty:
                    pass

                if pause_stdin[0]:
                    import time
                    time.sleep(0.05)
                    continue

                r, _, _ = select.select([sys.stdin.fileno(), wake_r], [], [], 0.05)
                if wake_r in r:
                    os.read(wake_r, 1)
                    continue
                if sys.stdin.fileno() in r and not pause_stdin[0]:
                    try:
                        data = os.read(sys.stdin.fileno(), 1024)
                        if not data:
                            break
                        if b'\r' in data or b'\n' in data:
                            cmd_byte_positions.append((len(client_buffer_bytes), None))
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
            import time
            tty.setraw(sys.stdin.fileno())
            response_iterator = self.stub.interact_node(request_generator())
            
            import queue
            response_queue = queue.Queue()
            
            def response_consumer():
                try:
                    for r in response_iterator:
                        response_queue.put(r)
                except Exception:
                    pass
                response_queue.put(None)
                
            t_consumer = threading.Thread(target=response_consumer, daemon=True)
            t_consumer.start()
            
            # First phase: Wait for connection status, print early data
            try:
                while True:
                    res = response_queue.get()
                    if res is None:
                        return
                    if res.stdout_data:
                        data = res.stdout_data
                        if debug:
                            data = data.replace(b'\x1b[H\x1b[2J', b'').replace(b'\x1bc', b'').replace(b'\x1b[3J', b'')
                        os.write(sys.stdout.fileno(), data)
                    
                    if res.success:
                        # Connection established on server, show success message
                        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)
                        printer.success(conn_msg)
                        pause_stdin[0] = False
                        tty.setraw(sys.stdin.fileno())
                        break
                    
                    if res.error_message:
                        # Connection failed on server
                        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)
                        printer.error(f"Connection failed: {res.error_message}")
                        return
            except queue.Empty:
                return
                
            # Second phase: Stream active session
            while True:
                res = response_queue.get()
                if res is None:
                    break
                if res.copilot_prompt:
                    pause_generator()
                    import json
                    import asyncio
                    import re
                    from rich.console import Console
                    from rich.panel import Panel
                    from rich.markdown import Markdown
                    from prompt_toolkit import PromptSession
                    from prompt_toolkit.key_binding import KeyBindings
                    from prompt_toolkit.formatted_text import HTML
                    from prompt_toolkit.history import InMemoryHistory
                    from ..printer import connpy_theme
                    from ..core import copilot_terminal_mode
                    
                    if not hasattr(self, 'copilot_history'):
                        self.copilot_history = InMemoryHistory()
                    
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)
                    import fcntl
                    flags = fcntl.fcntl(sys.stdin.fileno(), fcntl.F_GETFL)
                    fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
                    console = Console(theme=connpy_theme)
                    console.print("\n")
                    console.print(Panel(
                        "[bold cyan]AI Terminal Copilot[/bold cyan]\n"
                        "[dim]Type your question. Enter to send, Escape/Ctrl+C to cancel.\n"
                        "Tab to change context mode. Ctrl+\u2191/\u2193 to adjust context. \u2191\u2193 for question history.[/dim]",
                        border_style="cyan"
                    ))
                    
                    node_info = json.loads(res.copilot_node_info_json) if res.copilot_node_info_json else {}
                    
                    # Logic for context selection
                    blocks = []
                    raw_bytes = client_buffer_bytes
                    from ..core import node
                    dummy_node = node("dummy", "dummy") # For logclean
                    
                    if cmd_byte_positions and len(cmd_byte_positions) >= 2 and raw_bytes:
                        default_prompt = r'>$|#$|\$$|>.$|#.$|\$.$'
                        device_prompt = node_info.get("prompt", default_prompt)
                        prompt_re_str = re.sub(r'(?<!\\)\$', '', device_prompt)
                        try:
                            prompt_re = re.compile(prompt_re_str)
                        except Exception:
                            prompt_re = re.compile(re.sub(r'(?<!\\)\$', '', default_prompt))
                            
                        for i in range(1, len(cmd_byte_positions)):
                            pos, known_cmd = cmd_byte_positions[i]
                            prev_pos = cmd_byte_positions[i-1][0]
                            
                            if known_cmd:
                                # AI-injected command: we already know the command text
                                prev_chunk = raw_bytes[prev_pos:pos]
                                prev_cleaned = dummy_node._logclean(prev_chunk.decode(errors='replace'), var=True)
                                prev_lines = [l for l in prev_cleaned.split('\n') if l.strip()]
                                prompt_text = prev_lines[-1].strip() if prev_lines else ""
                                preview = f"{prompt_text}{known_cmd}" if prompt_text else known_cmd
                                blocks.append((pos, preview[:80]))
                            else:
                                # User-typed command: derive from raw log chunk
                                chunk = raw_bytes[prev_pos:pos]
                                cleaned = dummy_node._logclean(chunk.decode(errors='replace'), var=True)
                                lines = [l for l in cleaned.split('\n') if l.strip()]
                                preview = lines[-1].strip() if lines else ""
                                
                                if preview:
                                    match = prompt_re.search(preview)
                                    if match:
                                        cmd_text = preview[match.end():].strip()
                                        if cmd_text:
                                            blocks.append((pos, preview[:80]))
                        
                    clean_buffer = dummy_node._logclean(raw_bytes.decode(errors='replace'), var=True)
                    last_line = clean_buffer.split('\n')[-1].strip() if clean_buffer.strip() else "(prompt)"
                    blocks.append((len(raw_bytes), last_line[:80]))
                            
                    context_cmd = [1]
                    total_cmds = len(blocks)
                    total_lines = len(clean_buffer.split('\n'))
                    context_lines = [min(50, total_lines)]
                    context_mode = [0]
                    MODE_RANGE, MODE_SINGLE, MODE_LINES = 0, 1, 2
                    
                    bindings = KeyBindings()
                    
                    @bindings.add('c-up')
                    def _(event):
                        if context_mode[0] == MODE_LINES:
                            if context_lines[0] >= total_lines:
                                context_lines[0] = min(50, total_lines)
                            else:
                                context_lines[0] = min(context_lines[0] + 50, total_lines)
                        else:
                            if context_cmd[0] < total_cmds:
                                context_cmd[0] += 1
                            else:
                                context_cmd[0] = 1
                        event.app.invalidate()
                    
                    @bindings.add('c-down')
                    def _(event):
                        if context_mode[0] == MODE_LINES:
                            if context_lines[0] <= min(50, total_lines):
                                context_lines[0] = total_lines
                            else:
                                context_lines[0] = max(context_lines[0] - 50, min(50, total_lines))
                        else:
                            if context_cmd[0] > 1:
                                context_cmd[0] -= 1
                            else:
                                context_cmd[0] = total_cmds
                        event.app.invalidate()
                    
                    @bindings.add('tab')
                    def _(event):
                        context_mode[0] = (context_mode[0] + 1) % 3
                        event.app.invalidate()
                        
                    @bindings.add('escape')
                    def _(event):
                        event.app.exit(result='')
                        
                    def get_current_block():
                        idx = max(0, total_cmds - context_cmd[0])
                        return idx, blocks[idx]
                    
                    def get_active_buffer():
                        if context_mode[0] == MODE_LINES:
                            buffer_lines = clean_buffer.split('\n')
                            return '\n'.join(buffer_lines[-context_lines[0]:])
                        
                        idx, (start, preview) = get_current_block()
                        if context_mode[0] == MODE_SINGLE and idx + 1 < total_cmds:
                            end = blocks[idx + 1][0]
                            active_raw = raw_bytes[start:end]
                        else:
                            active_raw = raw_bytes[start:]
                        return preview + "\n" + dummy_node._logclean(active_raw.decode(errors='replace'), var=True)
                        
                    def get_prompt_text():
                        if context_mode[0] == MODE_LINES:
                            return HTML(f"<ansicyan>Ask [Ctx: {context_lines[0]}/{total_lines}L]: </ansicyan>")
                        
                        lines_count = len(get_active_buffer().split('\n'))
                        if context_mode[0] == MODE_SINGLE:
                            return HTML(f"<ansicyan>Ask [Cmd {context_cmd[0]} ~{lines_count}L]: </ansicyan>")
                        else:
                            return HTML(f"<ansicyan>Ask [Cmd {context_cmd[0]}\u2192END ~{lines_count}L]: </ansicyan>")
                        
                    def get_toolbar():
                        mode_labels = {MODE_RANGE: "RANGE", MODE_SINGLE: "SINGLE", MODE_LINES: "LINES"}
                        mode_label = mode_labels[context_mode[0]]
                        if context_mode[0] == MODE_LINES:
                            return HTML(f"<ansigray>\u25b6 Ctrl+\u2191/\u2193 adjusts by 50 lines  [Tab: {mode_label}]</ansigray>")
                        _, (_, preview) = get_current_block()
                        return HTML(f"<ansigray>\u25b6 {preview}  [Tab: {mode_label}]</ansigray>")
                    
                    try:
                        session = PromptSession(history=self.copilot_history)
                        question = session.prompt(get_prompt_text, key_bindings=bindings, bottom_toolbar=get_toolbar)
                    except KeyboardInterrupt:
                        question = ""

                    if not question or not question.strip() or question.strip() == "CANCEL":
                        console.print("\n[dim]Copilot cancelled.[/dim]")
                        request_queue.put(connpy_pb2.InteractRequest(copilot_question="CANCEL"))
                        resume_generator()
                        tty.setraw(sys.stdin.fileno())
                        continue

                    active_buffer = get_active_buffer()
                    request_queue.put(connpy_pb2.InteractRequest(copilot_question=question, copilot_context_buffer=active_buffer))
                    
                    from rich.live import Live
                    live_text = "Thinking..."
                    panel = Panel(live_text, title="[bold cyan]Copilot Guide[/bold cyan]", border_style="cyan")
                    result = {}
                    cancelled = False
                    
                    with copilot_terminal_mode(), Live(panel, console=console, refresh_per_second=10) as live:
                        import fcntl
                        flags = fcntl.fcntl(sys.stdin.fileno(), fcntl.F_GETFL)
                        fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, flags | os.O_NONBLOCK)
                        
                        while True:
                            try:
                                key = os.read(sys.stdin.fileno(), 1024)
                                if b'\x03' in key:
                                    cancelled = True
                                    request_queue.put(connpy_pb2.InteractRequest(copilot_question="CANCEL"))
                                    console.print("\n[dim]Copilot cancelled via Ctrl+C. Disconnecting...[/dim]")
                                    break
                            except OSError:
                                pass
                                
                            try:
                                chunk_res = response_queue.get(timeout=0.1)
                                if chunk_res is None:
                                    break
                                    
                                if chunk_res.copilot_stream_chunk:
                                    if live_text == "Thinking...": live_text = ""
                                    live_text += chunk_res.copilot_stream_chunk
                                    live.update(Panel(Markdown(live_text), title="[bold cyan]Copilot Guide[/bold cyan]", border_style="cyan"))
                                elif chunk_res.copilot_response_json:
                                    result = json.loads(chunk_res.copilot_response_json)
                                    break
                            except queue.Empty:
                                continue
                                
                        fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, flags)

                    if cancelled:
                        resume_generator()
                        tty.setraw(sys.stdin.fileno())
                        continue
                        
                    if result.get("error"):
                        console.print(f"[red]Error: {result['error']}[/red]")
                        request_queue.put(connpy_pb2.InteractRequest(copilot_action="cancel"))
                        resume_generator()
                        tty.setraw(sys.stdin.fileno())
                        continue

                    if live_text == "Thinking..." and result.get("guide"):
                        console.print(Panel(Markdown(result["guide"]), title="[bold cyan]Copilot Guide[/bold cyan]", border_style="cyan"))

                    commands = result.get("commands", [])
                    risk = result.get("risk_level", "low")
                    risk_style = {"low": "green", "high": "yellow", "destructive": "red"}.get(risk, "green")
                    
                    action_sent = "cancel"
                    if commands:
                        cmd_text = "\n".join(f"  {i+1}. {cmd}" for i, cmd in enumerate(commands))
                        console.print(Panel(
                            cmd_text,
                            title=f"[bold {risk_style}]Suggested Commands [{risk.upper()}][/bold {risk_style}]",
                            border_style=risk_style
                        ))
                        
                        try:
                            confirm_session = PromptSession()
                            confirm_bindings = KeyBindings()
                            @confirm_bindings.add('escape')
                            def _(event):
                                event.app.exit(result='n')
                            
                            pt_color = "ansi" + risk_style
                            action = confirm_session.prompt(
                                HTML(f"<{pt_color}>Send commands? (y/n/e/number/range) [n]: </{pt_color}>"),
                                key_bindings=confirm_bindings
                            )
                        except KeyboardInterrupt:
                            action = "n"
                            
                        if not action.strip():
                            action = "n"
                            
                        action_l = action.lower().strip()
                        if action_l in ('y', 'yes', 'all'):
                            action_sent = "send_all"
                        elif action_l.startswith('e'):
                            action_sent = f"edit_{action_l[1:]}" if len(action_l) > 1 else "edit_all"
                            # For remote editing, the client edits and sends back as custom action 
                            edit_session = PromptSession()
                            cmds_to_edit = []
                            if action_sent.startswith("edit_") and action_sent[5:].isdigit():
                                idx = int(action_sent[5:]) - 1
                                if 0 <= idx < len(commands):
                                    cmds_to_edit = [commands[idx]]
                            else:
                                cmds_to_edit = commands
                                
                            if cmds_to_edit:
                                target_cmd = "\n".join(cmds_to_edit)
                                try:
                                    edited_cmd = edit_session.prompt(
                                        HTML("<ansicyan>Edit commands (Alt+Enter or Esc,Enter to submit):\n</ansicyan>"),
                                        default=target_cmd,
                                        multiline=True
                                    )
                                    if edited_cmd.strip():
                                        action_sent = "custom:" + edited_cmd.strip()
                                    else:
                                        action_sent = "cancel"
                                except KeyboardInterrupt:
                                    action_sent = "cancel"
                        elif action_l not in ('n', 'no', ''):
                            action_sent = action_l
                    
                    console.print("[dim]Returning to session...[/dim]\n")
                    request_queue.put(connpy_pb2.InteractRequest(copilot_action=action_sent))
                    resume_generator()
                    tty.setraw(sys.stdin.fileno())
                    continue

                if res.copilot_injected_command:
                    cmd_byte_positions.append((len(client_buffer_bytes), res.copilot_injected_command))

                if res.stdout_data:
                    os.write(sys.stdout.fileno(), res.stdout_data)
                    client_buffer_bytes.extend(res.stdout_data)
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)
            os.close(wake_r)
            os.close(wake_w)

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
            timeout=timeout,
            name=kwargs.get("name", "")
        )
        if variables is not None:
            req.vars.CopyFrom(to_struct(variables))
            
        final_results = {}
        on_complete = kwargs.get("on_node_complete")
        
        for response in self.stub.run_commands(req):
            if on_complete:
                on_complete(response.unique_id, response.output, response.status)
            final_results[response.unique_id] = {
                "output": response.output,
                "status": response.status
            }
                
        return final_results

    @handle_errors
    def test_commands(self, nodes_filter, commands, expected, variables=None, parallel=10, timeout=10, prompt=None, **kwargs):
        nodes_list = [nodes_filter] if isinstance(nodes_filter, str) else list(nodes_filter)
        req = connpy_pb2.TestRequest(
            nodes=nodes_list,
            commands=commands,
            expected=expected if isinstance(expected, list) else [expected],
            folder=kwargs.get("folder", ""),
            prompt=prompt or "",
            parallel=parallel,
            timeout=timeout,
            name=kwargs.get("name", "")
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
                        
                        # Show prompt and wait for answer
                        prompt_text = Text.from_ansi(response.status_update)
                        ans = Prompt.ask(prompt_text)
                        
                        if status: 
                            status.update("[ai_status]Agent: Resuming...")
                            status.start()
                        
                        req_queue.put(connpy_pb2.AskRequest(confirmation_answer=ans))
                        continue
                        
                    if status:
                        status.update(response.status_update)
                    continue
                
                if response.debug_message:
                    if debug:
                        if live_display:
                            try: live_display.stop()
                            except: pass
                        if status:
                            try: status.stop()
                            except: pass
                        printer.console.print(Text.from_ansi(response.debug_message))
                        if live_display:
                            try: live_display.start()
                            except: pass
                        elif status:
                            try: status.start()
                            except: pass
                    continue
                
                if response.important_message:
                    if live_display:
                        try: live_display.stop()
                        except: pass
                    if status:
                        try: status.stop()
                        except: pass
                    printer.console.print(Text.from_ansi(response.important_message))
                    if live_display:
                        try: live_display.start()
                        except: pass
                    elif status:
                        try: status.start()
                        except: pass
                    continue

                if not response.is_final:
                    if response.text_chunk:
                        full_content += response.text_chunk
                        
                        if not live_display:
                            if status:
                                try: status.stop()
                                except: pass
                            
                            from rich.console import Console as RichConsole
                            from ..printer import connpy_theme, get_original_stdout
                            stable_console = RichConsole(theme=connpy_theme, file=get_original_stdout())
                            
                            # We default to Engineer title during stream, final result will correct it if needed
                            live_display = Live(
                                Panel(Markdown(full_content), title="[bold engineer]Network Engineer[/bold engineer]", border_style="engineer", expand=False),
                                console=stable_console,
                                refresh_per_second=8,
                                transient=False
                            )
                            live_display.start()
                        else:
                            live_display.update(
                                Panel(Markdown(full_content), title="[bold engineer]Network Engineer[/bold engineer]", border_style="engineer", expand=False)
                            )
                    continue
                
                if response.is_final:
                    if live_display:
                        try: live_display.stop()
                        except: pass
                    # Final stop for status to ensure it disappears before the panel
                    if status:
                        try: status.stop()
                        except: pass

                    final_result = from_struct(response.full_result)
                    responder = final_result.get("responder", "engineer")
                    alias = "architect" if responder == "architect" else "engineer"
                    role_label = "Network Architect" if responder == "architect" else "Network Engineer"
                    title = f"[bold {alias}]{role_label}[/bold {alias}]"
                    
                    content_to_print = full_content or final_result.get("response", "")
                    if content_to_print:
                        if live_display:
                            # Re-render the final frame with correct title/colors
                            live_display.update(Panel(Markdown(content_to_print), title=title, border_style=alias, expand=False))
                        else:
                            printer.console.print(Panel(Markdown(content_to_print), title=title, border_style=alias, expand=False))
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
