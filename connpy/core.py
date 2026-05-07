#!/usr/bin/env python3
#Imports
import os
import re
import pexpect
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
import ast
from time import sleep,time
import datetime
import sys
import threading
from pathlib import Path
from copy import deepcopy
from .hooks import ClassHook, MethodHook
import io
import asyncio
import fcntl
from . import printer
from .tunnels import LocalStream


#functions and classes
@ClassHook
class node:
    ''' This class generates a node object. Containts all the information and methods to connect and interact with a device using ssh or telnet.

    ### Attributes:  

        - output (str): Output of the commands you ran with run or test 
                        method.  

        - result(bool): True if expected value is found after running 
                        the commands using test method.

        - status (int): 0 if the method run or test run successfully.
                        1 if connection failed.
                        2 if expect timeouts without prompt or EOF.

        '''
    
    def __init__(self, unique, host, options='', logs='', password='', port='', protocol='', user='', config='', tags='', jumphost=''):
        ''' 
            
        ### Parameters:  

            - unique (str): Unique name to assign to the node.

            - host   (str): IP address or hostname of the node.

        ### Optional Parameters:  

            - options  (str): Additional options to pass the ssh/telnet for
                              connection.  

            - logs     (str): Path/file for storing the logs. You can use 
                              ${unique},${host}, ${port}, ${user}, ${protocol} 
                              as variables.  

            - password (str): Encrypted or plaintext password.  

            - port     (str): Port to connect to node, default 22 for ssh and 23 
                              for telnet.  

            - protocol (str): Select ssh, telnet, kubectl or docker. Default is ssh.  

            - user     (str): Username to of the node.  

            - config   (obj): Pass the object created with class configfile with 
                              key for decryption and extra configuration if you 
                              are using connection manager.  

            - tags   (dict) : Tags useful for automation and personal porpuse
                              like "os", "prompt" and "screenleght_command"
                              
            - jumphost (str): Reference another node to be used as a jumphost
        '''
        self.config = config
        if config == '':
            self.idletime = 0
            self.key = None
        else:
            self.idletime = config.config["idletime"]
            self.key = config.key
        self.unique = unique
        attr = {"host": host, "logs": logs, "options":options, "port": port, "protocol": protocol, "user": user, "tags": tags, "jumphost": jumphost}
        for key in attr:
            profile = re.search("^@(.*)", str(attr[key]))
            if profile and config != '':
                try:
                    setattr(self,key,config.profiles[profile.group(1)][key])
                except KeyError:
                    setattr(self,key,"")
            elif attr[key] == '' and key == "protocol":
                try:
                    setattr(self,key,config.profiles["default"][key])
                except (KeyError, AttributeError):
                    setattr(self,key,"ssh")
            else: 
                setattr(self,key,attr[key])
        if isinstance(password,list):
            self.password = []
            for i, s in enumerate(password):
                profile = re.search("^@(.*)", password[i])
                if profile and config != '':
                    self.password.append(config.profiles[profile.group(1)]["password"])
                else:
                    self.password.append(password[i])
        else:
            self.password = [password]
        if self.jumphost != "" and config != '':
            self.jumphost = config.getitem(self.jumphost)
            for key in self.jumphost:
                profile = re.search("^@(.*)", str(self.jumphost[key]))
                if profile:
                    try:
                        self.jumphost[key] = config.profiles[profile.group(1)][key]
                    except KeyError:
                        self.jumphost[key] = ""
                elif self.jumphost[key] == '' and key == "protocol":
                    try:
                        self.jumphost[key] = config.profiles["default"][key]
                    except KeyError:
                        self.jumphost[key] = "ssh"
            if isinstance(self.jumphost["password"],list):
                jumphost_password = []
                for i, s in enumerate(self.jumphost["password"]):
                    profile = re.search("^@(.*)", self.jumphost["password"][i])
                    if profile:
                        jumphost_password.append(config.profiles[profile.group(1)]["password"])
                    else:
                        jumphost_password.append(self.jumphost["password"][i])
                self.jumphost["password"] = jumphost_password
            else:
                self.jumphost["password"] = [self.jumphost["password"]]
            if self.jumphost["password"] != [""]:
                self.password = self.jumphost["password"] + self.password

            if self.jumphost["protocol"] == "ssh":
                jumphost_cmd = self.jumphost["protocol"] + " -W %h:%p"
                if self.jumphost["port"] != '':
                    jumphost_cmd = jumphost_cmd + " -p " + self.jumphost["port"]
                if self.jumphost["options"] != '':
                    jumphost_cmd = jumphost_cmd + " " + self.jumphost["options"]
                if self.jumphost["user"] == '':
                    jumphost_cmd = jumphost_cmd + " {}".format(self.jumphost["host"])
                else:
                    jumphost_cmd = jumphost_cmd + " {}".format("@".join([self.jumphost["user"],self.jumphost["host"]]))
                self.jumphost = f"-o ProxyCommand=\"{jumphost_cmd}\""
            elif self.jumphost["protocol"] == "ssm":
                ssm_target = self.jumphost["host"]
                ssm_cmd = f"aws ssm start-session --target {ssm_target} --document-name AWS-StartSSHSession --parameters 'portNumber=22'"
                if isinstance(self.jumphost.get("tags"), dict):
                    if "profile" in self.jumphost["tags"]:
                        ssm_cmd += f" --profile {self.jumphost['tags']['profile']}"
                    if "region" in self.jumphost["tags"]:
                        ssm_cmd += f" --region {self.jumphost['tags']['region']}"
                if self.jumphost["options"] != '':
                    ssm_cmd += f" {self.jumphost['options']}"
                
                bastion_user_part = f"{self.jumphost['user']}@{ssm_target}" if self.jumphost['user'] else ssm_target
                
                ssh_opts = ""
                if isinstance(self.jumphost.get("tags"), dict) and "ssh_options" in self.jumphost["tags"]:
                    ssh_opts = f" {self.jumphost['tags']['ssh_options']}"
                
                inner_ssh = f"ssh{ssh_opts} -o ProxyCommand='{ssm_cmd}' -W %h:%p {bastion_user_part}"
                self.jumphost = f"-o ProxyCommand=\"{inner_ssh}\""
            elif self.jumphost["protocol"] in ["kubectl", "docker"]:
                nc_cmd = "nc"
                if isinstance(self.jumphost.get("tags"), dict) and "nc_command" in self.jumphost["tags"]:
                    nc_cmd = self.jumphost["tags"]["nc_command"]
                    
                if self.jumphost["protocol"] == "kubectl":
                    proxy_cmd = f"kubectl exec "
                    if self.jumphost["options"] != '':
                        proxy_cmd += f"{self.jumphost['options']} "
                    proxy_cmd += f"{self.jumphost['host']} -i -- {nc_cmd} %h %p"
                else:
                    proxy_cmd = f"docker "
                    if self.jumphost["options"] != '':
                        proxy_cmd += f"{self.jumphost['options']} "
                    proxy_cmd += f"exec -i {self.jumphost['host']} {nc_cmd} %h %p"
                    
                self.jumphost = f"-o ProxyCommand=\"{proxy_cmd}\""
            else:
                self.jumphost = ""
        
        self.output = ""
        self.status = 1
        self.result = {}

    @MethodHook
    def _passtx(self, passwords, *, keyfile=None):
        # decrypts passwords, used by other methdos.
        dpass = []
        if keyfile is None:
            keyfile = self.key
        if keyfile is not None:
            with open(keyfile) as f:
                key = RSA.import_key(f.read())
            decryptor = PKCS1_OAEP.new(key)
        for passwd in passwords:
            if not re.match('^b[\"\'].+[\"\']$', passwd):
                dpass.append(passwd)
            else:
                try:
                    decrypted = decryptor.decrypt(ast.literal_eval(passwd)).decode("utf-8")
                    dpass.append(decrypted)
                except Exception:
                    printer.error("Decryption failed: Missing or corrupted key.")
                    printer.info("Verify your RSA key and configuration settings.")
                    sys.exit(1)
        return dpass

    

    @MethodHook
    def _logfile(self, logfile = None):
        # translate logs variables and generate logs path.
        if logfile == None:
            logfile = self.logs
        logfile = logfile.replace("${unique}", self.unique)
        logfile = logfile.replace("${host}", self.host)
        logfile = logfile.replace("${port}", self.port)
        logfile = logfile.replace("${user}", self.user)
        logfile = logfile.replace("${protocol}", self.protocol)
        now = datetime.datetime.now()
        dateconf = re.search(r'\$\{date \'(.*)\'}', logfile)
        if dateconf:
            logfile = re.sub(r'\$\{date (.*)}',now.strftime(dateconf.group(1)), logfile)
        return logfile

    @MethodHook
    def _logclean(self, logfile, var = False):
        # Remove special ascii characters and process terminal cursor movements to clean logs.
        if var == False:
            t = open(logfile, "r").read()
        else:
            t = logfile
            
        lines = t.split('\n')
        cleaned_lines = []
        
        # Regex to capture: ANSI sequences, control characters (\r, \b, etc), and plain text chunks
        token_re = re.compile(r'(\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/ ]*[@-~])|\r|\b|\x7f|[\x00-\x1F]|[^\x1B\r\b\x7f\x00-\x1F]+)')
        
        for line in lines:
            buffer = []
            cursor = 0
            
            for token in token_re.findall(line):
                if token == '\r':
                    cursor = 0
                elif token in ('\b', '\x7f'):
                    if cursor > 0:
                        cursor -= 1
                elif token == '\x1B[D': # Left Arrow
                    if cursor > 0:
                        cursor -= 1
                elif token == '\x1B[C': # Right Arrow
                    if cursor < len(buffer):
                        cursor += 1
                elif token == '\x1B[K': # Clear to end of line
                    buffer = buffer[:cursor]
                elif token.startswith('\x1B'):
                    # Ignore other ANSI sequences (colors, etc)
                    continue
                elif len(token) == 1 and ord(token) < 32:
                    # Ignore other non-printable control chars
                    continue
                else:
                    # Regular printable text
                    for char in token:
                        if cursor == len(buffer):
                            buffer.append(char)
                        else:
                            buffer[cursor] = char
                        cursor += 1
            cleaned_lines.append("".join(buffer))
            
        t = "\n".join(cleaned_lines).replace('\n\n', '\n').strip()

        if var == False:
            d = open(logfile, "w")
            d.write(t)
            d.close()
            return
        else:
            return t

    @MethodHook
    def _savelog(self):
        '''Save the log buffer to the file at regular intervals if there are changes.'''
        t = threading.current_thread()
        prev_size = 0  # Store the previous size of the buffer

        while getattr(t, "do_run", True):  # Check if thread is signaled to stop
            current_size = self.mylog.tell()  # Current size of the buffer

            # Only save if the buffer size has changed
            if current_size != prev_size:
                with open(self.logfile, "w") as f:  # Use "w" to overwrite the file
                    f.write(self._logclean(self.mylog.getvalue().decode(), True))
                prev_size = current_size  # Update the previous size
            sleep(5)

    @MethodHook
    def _filter(self, a):
        #Set time for last input when using interact
        self.lastinput = time()
        return a

    @MethodHook
    def _keepalive(self):
        #Send keepalive ctrl+e when idletime passed without new inputs on interact
        self.lastinput = time()
        t = threading.current_thread()
        while True:
            if time() - self.lastinput >= self.idletime:
                self.child.sendcontrol("e")
                self.lastinput = time()
            sleep(1)


    def _setup_interact_environment(self, debug=False, logger=None, async_mode=False):
        size = re.search('columns=([0-9]+).*lines=([0-9]+)',str(os.get_terminal_size()))
        self.child.setwinsize(int(size.group(2)),int(size.group(1)))
        if logger:
            port_str = f":{self.port}" if self.port and self.protocol not in ["ssm", "kubectl", "docker"] else ""
            logger("success", f"Connected to {self.unique} at {self.host}{port_str} via: {self.protocol}")

        if 'logfile' in dir(self):
            # Initialize self.mylog
            if not 'mylog' in dir(self):
                self.mylog = io.BytesIO()
            if not async_mode:
                self.child.logfile_read = self.mylog
                
                # Start the _savelog thread
                log_thread = threading.Thread(target=self._savelog)
                log_thread.daemon = True
                log_thread.start()
        if 'missingtext' in dir(self):
            print(self.child.after.decode(), end='')
        if self.idletime > 0 and not async_mode:
            x = threading.Thread(target=self._keepalive)
            x.daemon = True
            x.start()
        if debug:
            if 'mylog' in dir(self):
                if not async_mode:
                    print(self.mylog.getvalue().decode())

    def _teardown_interact_environment(self):
        if 'logfile' in dir(self) and hasattr(self, 'mylog'):
            with open(self.logfile, "w") as f:
                f.write(self._logclean(self.mylog.getvalue().decode(), True))

    async def _async_interact_loop(self, local_stream, resize_callback, copilot_handler=None):
        local_stream.setup(resize_callback=resize_callback)
        try:
            child_fd = self.child.child_fd
            
            # 1. Flush ghost buffer (Clean UX)
            ghost_buffer = b''
            if getattr(self, 'missingtext', False):
                # If we are missing the password, we MUST show the password prompt
                ghost_buffer = (self.child.after or b'') + (self.child.buffer or b'')
            else:
                # We auto-logged in. Hide the messy password negotiation and just keep any pending live stream.
                ghost_buffer = self.child.buffer or b''

            # Fix user's pet peeve: Strip leading newlines to avoid the empty lines 
            # the router echoes after receiving the password or blank line.
            if not getattr(self, 'missingtext', False):
                ghost_buffer = ghost_buffer.lstrip(b'\r\n ')

            if ghost_buffer:
                # Add a single clean newline so it doesn't merge with the Connected message
                await local_stream.write(b'\r\n' + ghost_buffer)
                if hasattr(self, 'mylog'):
                    self.mylog.write(b'\n' + ghost_buffer)
                    
            self.child.buffer = b''
            self.child.before = b''
            
            # 2. Set child fd non-blocking
            flags = fcntl.fcntl(child_fd, fcntl.F_GETFL)
            fcntl.fcntl(child_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            
            loop = asyncio.get_running_loop()
            child_reader_queue = asyncio.Queue()
            
            def _child_read_ready():
                try:
                    data = os.read(child_fd, 4096)
                    if data:
                        child_reader_queue.put_nowait(data)
                    else:
                        child_reader_queue.put_nowait(b'')
                except BlockingIOError:
                    pass
                except OSError:
                    child_reader_queue.put_nowait(b'')
                    
            loop.add_reader(child_fd, _child_read_ready)
            self.lastinput = time()
            
            async def ingress_task():
                while True:
                    data = await local_stream.read()
                    if not data:
                        break
                    
                    # Copilot interception
                    if copilot_handler and b'\x00' in data:
                        # Extract clean buffer from session log
                        buffer = ""
                        if hasattr(self, 'mylog'):
                            raw = self.mylog.getvalue().decode(errors='replace')
                            buffer = self._logclean(raw, var=True)
                            # Pass the full buffer to the handler so the user can adjust context size interactively
                        
                        # Build node info from available metadata
                        node_info = {"name": getattr(self, 'unique', 'unknown'), "host": getattr(self, 'host', 'unknown')}
                        if isinstance(getattr(self, 'tags', None), dict):
                            node_info["os"] = self.tags.get("os", "unknown")
                        
                        # Invoke copilot (async callback handles UI)
                        await copilot_handler(buffer, node_info, local_stream, child_fd)
                        continue
                    
                    # Remove any stray \x00 bytes and forward normally
                    clean_data = data.replace(b'\x00', b'')
                    if clean_data:
                        try:
                            os.write(child_fd, clean_data)
                        except OSError:
                            break
                        self.lastinput = time()
                    
            async def egress_task():
                # Continue stripping newlines from the live stream until we hit real text
                skip_newlines = not getattr(self, 'missingtext', False) and not ghost_buffer
                while True:
                    data = await child_reader_queue.get()
                    if not data:
                        break
                        
                    if skip_newlines:
                        stripped = data.lstrip(b'\r\n')
                        if stripped:
                            skip_newlines = False
                            data = stripped
                        else:
                            continue
                            
                    await local_stream.write(data)
                    if hasattr(self, 'mylog'):
                        self.mylog.write(data)
                        
            async def keepalive_task():
                while True:
                    await asyncio.sleep(1)
                    if time() - self.lastinput >= self.idletime:
                        try:
                            self.child.sendcontrol("e")
                            self.lastinput = time()
                        except Exception:
                            pass
                            
            async def savelog_task():
                prev_size = 0
                while True:
                    await asyncio.sleep(5)
                    current_size = self.mylog.tell()
                    if current_size != prev_size:
                        try:
                            with open(self.logfile, "w") as f:
                                f.write(self._logclean(self.mylog.getvalue().decode(), True))
                            prev_size = current_size
                        except Exception:
                            pass

            try:
                # gather runs until any task completes (or we just let them run until EOF breaks them)
                # Ingress breaks on user EOF. Egress breaks on child EOF. 
                # We want to exit if either happens, so return_exceptions=False, but we need to cancel the others.
                tasks = [
                    asyncio.create_task(ingress_task()),
                    asyncio.create_task(egress_task())
                ]
                if self.idletime > 0:
                    tasks.append(asyncio.create_task(keepalive_task()))
                if hasattr(self, 'logfile') and hasattr(self, 'mylog'):
                    tasks.append(asyncio.create_task(savelog_task()))
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for p in pending:
                    p.cancel()
            finally:
                loop.remove_reader(child_fd)
                try:
                    flags = fcntl.fcntl(child_fd, fcntl.F_GETFL)
                    fcntl.fcntl(child_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
                except Exception:
                    pass
        finally:
            local_stream.teardown()


    @MethodHook
    def interact(self, debug=False, logger=None):
        '''
        Asynchronous interactive session using Smart Tunnel architecture.
        Allows multiplexing I/O and handling SIGWINCH events locally without blocking.
        '''
        connect = self._connect(debug=debug, logger=logger)
        if connect == True:
            try:
                self._setup_interact_environment(debug=debug, logger=logger, async_mode=True)
                
                local_stream = LocalStream()
                
                def resize_callback(rows, cols):
                    try:
                        self.child.setwinsize(rows, cols)
                    except Exception:
                        pass
                
                # Build local copilot handler
                copilot_handler = self._build_local_copilot_handler()
                
                asyncio.run(self._async_interact_loop(local_stream, resize_callback, copilot_handler=copilot_handler))
            finally:
                self._teardown_interact_environment()
        else:
            if logger:
                logger("error", str(connect))
            else:
                printer.error(f"Connection failed: {str(connect)}")
            sys.exit(1)

    def _build_local_copilot_handler(self):
        """Build copilot handler for local CLI sessions using rich for rendering."""
        config = getattr(self, 'config', None) if hasattr(self, 'config') else None
        if not config:
            return None
        
        # Persistent history across copilot invocations within the same session
        from prompt_toolkit.history import InMemoryHistory
        copilot_history = InMemoryHistory()
        
        async def handler(buffer, node_info, stream, child_fd):
            import termios, tty
            import asyncio
            import os
            import sys
            
            try:
                # Disable LocalStream reader so it doesn't steal keystrokes from Prompt
                loop = asyncio.get_running_loop()
                loop.remove_reader(sys.stdin.fileno())
                
                # Override SIGINT so asyncio doesn't kill the event loop when we press Ctrl+C
                import signal
                orig_sigint = signal.getsignal(signal.SIGINT)
                def custom_sigint(sig, frame):
                    raise KeyboardInterrupt()
                signal.signal(signal.SIGINT, custom_sigint)
                
                # 1. Salir de raw mode para poder usar input() y rich
                stdin_fd = sys.stdin.fileno()
                
                # Get true original settings saved before entering raw mode
                original_settings = getattr(stream, 'original_tty_settings', None)
                if original_settings:
                    import copy
                    new_settings = copy.deepcopy(original_settings)
                    new_settings[3] = new_settings[3] & ~termios.ECHOCTL
                    termios.tcsetattr(stdin_fd, termios.TCSADRAIN, new_settings)
                
                # Remove O_NONBLOCK from stdin so Prompt.ask() works
                import fcntl
                flags = fcntl.fcntl(stdin_fd, fcntl.F_GETFL)
                fcntl.fcntl(stdin_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
                
                # Force a carriage return so the UI doesn't start mid-line
                sys.stdout.write('\r\n')
                sys.stdout.flush()
                
                from rich.console import Console
                from rich.panel import Panel
                from rich.markdown import Markdown
                from rich.prompt import Prompt
                from .printer import connpy_theme

                
                console = Console(theme=connpy_theme)
                console.print("\n")
                console.print(Panel(
                    "[bold cyan]AI Terminal Copilot[/bold cyan]\n"
                    "[dim]Type your question. Enter to send, Escape/Ctrl+C to cancel.\n"
                    "Ctrl+\u2191/\u2193 to adjust context lines. \u2191\u2193 for question history.[/dim]",
                    border_style="cyan"
                ))
                
                # 2. Capturar pregunta del usuario
                total_lines = len(buffer.split('\n'))
                context_lines = [min(50, total_lines)]
                cancelled = [False]
                
                from prompt_toolkit import PromptSession
                from prompt_toolkit.key_binding import KeyBindings
                from prompt_toolkit.formatted_text import HTML
                
                bindings = KeyBindings()
                
                @bindings.add('c-up')
                def _(event):
                    if context_lines[0] >= total_lines:
                        context_lines[0] = min(50, total_lines)
                    else:
                        context_lines[0] = min(context_lines[0] + 50, total_lines)
                    event.app.invalidate()
                
                @bindings.add('c-down')
                def _(event):
                    if context_lines[0] <= min(50, total_lines):
                        context_lines[0] = total_lines
                    else:
                        context_lines[0] = max(context_lines[0] - 50, min(50, total_lines))
                    event.app.invalidate()
                
                @bindings.add('escape')
                def _(event):
                    cancelled[0] = True
                    event.app.exit(result='')
                
                def get_prompt_text():
                    return HTML(f"<ansicyan>Ask [Ctx: {context_lines[0]}/{total_lines}L]: </ansicyan>")
                
                session = PromptSession(history=copilot_history)
                question = await session.prompt_async(get_prompt_text, key_bindings=bindings)
                if cancelled[0] or not question.strip():
                    console.print("\n[dim]Copilot cancelled.[/dim]")
                    os.write(child_fd, b'\x15\r')
                    return
                
                # Slice the buffer dynamically based on selected context
                buffer_lines = buffer.split('\n')
                active_buffer = '\n'.join(buffer_lines[-context_lines[0]:])
                
                # 3. Llamar al AI con spinner
                from .services.ai_service import AIService
                service = AIService(config)
                
                past_questions = copilot_history.get_strings()
                if len(past_questions) > 1:
                    # Limit history to last 5 questions to save tokens, excluding current
                    recent_history = past_questions[-6:-1]
                    history_text = "\n".join(f"- {q}" for q in recent_history)
                    enriched_question = f"Previous questions in this session:\n{history_text}\n\nCurrent Question:\n{question}"
                else:
                    enriched_question = question
                
                from rich.live import Live
                
                live_text = "Thinking..."
                panel = Panel(live_text, title="[bold cyan]Copilot Guide[/bold cyan]", border_style="cyan")
                
                def on_chunk(text):
                    nonlocal live_text
                    if live_text == "Thinking...":
                        live_text = ""
                    live_text += text
                    try:
                        # Use call_soon_threadsafe if possible, but rich Live is thread-safe enough
                        loop.call_soon_threadsafe(
                            lambda: live.update(Panel(Markdown(live_text), title="[bold cyan]Copilot Guide[/bold cyan]", border_style="cyan"))
                        )
                    except Exception:
                        live.update(Panel(Markdown(live_text), title="[bold cyan]Copilot Guide[/bold cyan]", border_style="cyan"))
                
                with Live(panel, console=console, refresh_per_second=10) as live:
                    result = await asyncio.to_thread(service.ask_copilot, active_buffer, enriched_question, node_info, chunk_callback=on_chunk)
                
                if result.get("error"):
                    console.print(f"[red]Error: {result['error']}[/red]")
                    return
                
                # If nothing was streamed (fallback), or to ensure final state
                if live_text == "Thinking..." and result.get("guide"):
                    console.print(Panel(
                        Markdown(result["guide"]),
                        title="[bold cyan]Copilot Guide[/bold cyan]",
                        border_style="cyan"
                    ))
                
                commands = result.get("commands", [])
                risk = result.get("risk_level", "low")
                risk_style = {"low": "green", "high": "yellow", "destructive": "red"}.get(risk, "green")
                
                if commands:
                    cmd_text = "\n".join(f"  {i+1}. {cmd}" for i, cmd in enumerate(commands))
                    console.print(Panel(
                        cmd_text,
                        title=f"[bold {risk_style}]Suggested Commands [{risk.upper()}][/bold {risk_style}]",
                        border_style=risk_style
                    ))
                    
                    # 5. Preguntar si inyectar (usando prompt_toolkit)
                    confirm_session = PromptSession()
                    confirm_bindings = KeyBindings()
                    
                    @confirm_bindings.add('escape')
                    def _(event):
                        event.app.exit(result='n')
                    
                    pt_color = "ansi" + risk_style
                    action = await confirm_session.prompt_async(
                        HTML(f"<{pt_color}>Send commands? (y/n/e/number/range) [n]: </{pt_color}>"),
                        key_bindings=confirm_bindings
                    )
                    
                    if not action.strip():
                        action = "n"
                        
                    console.print("[dim]Returning to session...[/dim]\n")
                    
                    action_l = action.lower().strip()
                    if action_l in ('y', 'yes', 'all'):
                        os.write(child_fd, b'\x15')  # Ctrl+U to clear line
                        await asyncio.sleep(0.1)
                        for cmd in commands:
                            os.write(child_fd, (cmd + "\n").encode())
                            await asyncio.sleep(0.3)
                    elif action_l.startswith('e'):
                        # Edit mode
                        edit_session = PromptSession()
                        cmds_to_edit = []
                        
                        if len(action_l) > 1 and action_l[1:].isdigit():
                            idx = int(action_l[1:]) - 1
                            if 0 <= idx < len(commands):
                                cmds_to_edit = [commands[idx]]
                        else:
                            cmds_to_edit = commands
                            
                        if cmds_to_edit:
                            target_cmd = "\n".join(cmds_to_edit)
                            try:
                                edited_cmd = await edit_session.prompt_async(
                                    HTML("<ansicyan>Edit commands (Alt+Enter or Esc,Enter to submit):\n</ansicyan>"),
                                    default=target_cmd,
                                    multiline=True
                                )
                                if edited_cmd.strip():
                                    os.write(child_fd, b'\x15')
                                    await asyncio.sleep(0.1)
                                    for cmd in edited_cmd.split('\n'):
                                        if cmd.strip():
                                            os.write(child_fd, (cmd.strip() + "\n").encode())
                                            await asyncio.sleep(0.3)
                                else:
                                    os.write(child_fd, b'\x15\r')
                            except KeyboardInterrupt:
                                os.write(child_fd, b'\x15\r')
                        else:
                            os.write(child_fd, b'\x15\r')
                    elif action_l not in ('n', 'no', ''):
                        try:
                            selected_indices = set()
                            for part in action_l.split(','):
                                part = part.strip()
                                if not part: continue
                                if '-' in part:
                                    start_str, end_str = part.split('-', 1)
                                    start = int(start_str) - 1
                                    end = int(end_str) - 1
                                    for i in range(start, end + 1):
                                        selected_indices.add(i)
                                else:
                                    selected_indices.add(int(part) - 1)
                            
                            valid_indices = sorted([i for i in selected_indices if 0 <= i < len(commands)])
                            if valid_indices:
                                os.write(child_fd, b'\x15')  # Ctrl+U to clear line
                                await asyncio.sleep(0.1)
                                if len(valid_indices) == 1:
                                    os.write(child_fd, (commands[valid_indices[0]] + "\n").encode())
                                else:
                                    for idx in valid_indices:
                                        os.write(child_fd, (commands[idx] + "\n").encode())
                                        await asyncio.sleep(0.3)
                            else:
                                os.write(child_fd, b'\x15\r')  # Ctrl+U + Enter to abort line and get new prompt
                        except ValueError:
                            os.write(child_fd, b'\x15\r')
                    else:
                        os.write(child_fd, b'\x15\r')
                else:
                    console.print("[dim]Returning to session...[/dim]\n")
                    os.write(child_fd, b'\x15\r')
            except KeyboardInterrupt:
                if 'console' in locals():
                    console.print("\n[dim]Copilot cancelled via Ctrl+C.[/dim]\n")
                else:
                    print("\n[dim]Copilot cancelled via Ctrl+C.[/dim]\n")
                os.write(child_fd, b'\x15\r')
            except Exception as e:
                import traceback
                print(f"\n[ERROR in Copilot Handler] {e}", flush=True)
                traceback.print_exc()
            finally:
                # 6. Restaurar raw mode, O_NONBLOCK y SIGINT
                tty.setraw(stdin_fd)
                fcntl.fcntl(stdin_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                if 'orig_sigint' in locals():
                    signal.signal(signal.SIGINT, orig_sigint)
                
                # Re-enable LocalStream reader
                try:
                    loop = asyncio.get_running_loop()
                    loop.add_reader(stdin_fd, stream._read_ready)
                except Exception:
                    pass
        
        return handler


    @MethodHook
    def run(self, commands, vars = None,*, folder = '', prompt = r'>$|#$|\$$|>.$|#.$|\$.$', stdout = False, timeout = 10, logger = None):
        '''
        Run a command or list of commands on the node and return the output.


        ### Parameters:  

            - commands (str/list): Commands to run on the node. Should be 
                                   str or a list of str. You can use variables
                                   as {varname} and defining them in optional
                                   parameter vars.

        ### Optional Parameters:  

            - vars  (dict): Dictionary containing the definition of variables
                            used in commands parameter.
                            Keys: Variable names.
                            Values: strings.

        ### Optional Named Parameters:  

            - folder (str): Path where output log should be stored, leave 
                            empty to disable logging.  

            - prompt (str): Prompt to be expected after a command is finished 
                            running. Usually linux uses  ">" or EOF while 
                            routers use ">" or "#". The default value should 
                            work for most nodes. Change it if your connection 
                            need some special symbol.  

            - stdout (bool):Set True to send the command output to stdout. 
                            default False.

            - timeout (int):Time in seconds for expect to wait for prompt/EOF.
                            default 10.

        ### Returns:  

            str: Output of the commands you ran on the node.

        '''
        connect = self._connect(timeout = timeout, logger = logger)
        now = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
        if connect == True:
            if logger:
                port_str = f":{self.port}" if self.port and self.protocol not in ["ssm", "kubectl", "docker"] else ""
                logger("success", f"Connected to {self.unique} at {self.host}{port_str} via: {self.protocol}")

            # Attempt to set the terminal size
            try:
                self.child.setwinsize(65535, 65535)
            except Exception:
                try:
                    self.child.setwinsize(10000, 10000)
                except Exception:
                    pass
            if "prompt" in self.tags:
                prompt = self.tags["prompt"]
            expects = [prompt, pexpect.EOF, pexpect.TIMEOUT]

            output = ''
            status = ''
            if not isinstance(commands, list):
                commands = [commands]
            if "screen_length_command" in self.tags:
                commands.insert(0, self.tags["screen_length_command"])
            self.mylog = io.BytesIO()
            self.child.logfile_read = self.mylog
            for c in commands:
                if vars is not None:
                    try:
                        c = c.format(**vars)
                    except KeyError as e:
                        self.output = f"Error: Variable {e} not defined in task or inventory"
                        self.status = 1
                        return self.output
                result = self.child.expect(expects, timeout = timeout)
                self.child.sendline(c)
                if result == 2:
                    break
            if not result == 2:
                result = self.child.expect(expects, timeout = timeout)
            self.child.close()
            output = self._logclean(self.mylog.getvalue().decode(), True)
            if logger:
                logger("output", output)
            if folder != '':
                with open(folder + "/" + self.unique + "_" + now + ".txt", "w") as f:
                    f.write(output)
                    f.close()
            self.output = output
            if result == 2:
                self.status = 2
            else:
                self.status = 0
            return output
        else:
            self.output = connect
            self.status = 1
            if logger:
                logger("error", f"Connection failed: {connect}")
            if folder != '':
                with open(folder + "/" + self.unique + "_" + now + ".txt", "w") as f:
                    f.write(connect)

                    f.close()
            return connect

    @MethodHook
    def test(self, commands, expected, vars = None,*, folder = '', prompt = r'>$|#$|\$$|>.$|#.$|\$.$', timeout = 10, logger = None):
        '''
        Run a command or list of commands on the node, then check if expected value appears on the output after the last command.


        ### Parameters:  

            - commands (str/list): Commands to run on the node. Should be
                                   str or a list of str. You can use variables
                                   as {varname} and defining them in optional
                                   parameter vars.

            - expected (str)     : Expected text to appear after running 
                                   all the commands on the node.You can use
                                   variables as {varname} and defining them
                                   in optional parameter vars.

        ### Optional Parameters:  

            - vars  (dict): Dictionary containing the definition of variables
                            used in commands and expected parameters.
                            Keys: Variable names.
                            Values: strings.

        ### Optional Named Parameters: 

            - folder (str): Path where output log should be stored, leave 
                            empty to not store logs.

            - prompt (str): Prompt to be expected after a command is finished
                            running. Usually linux uses  ">" or EOF while 
                            routers use ">" or "#". The default value should 
                            work for most nodes. Change it if your connection 
                            need some special symbol.

            - timeout (int):Time in seconds for expect to wait for prompt/EOF.
                            default 10.

        ### Returns: 
            bool: true if expected value is found after running the commands 
                  false if prompt is found before.

        '''
        now = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        connect = self._connect(timeout = timeout, logger = logger)
        if connect == True:
            if logger:
                port_str = f":{self.port}" if self.port and self.protocol not in ["ssm", "kubectl", "docker"] else ""
                logger("success", f"Connected to {self.unique} at {self.host}{port_str} via: {self.protocol}")

            # Attempt to set the terminal size
            try:
                self.child.setwinsize(65535, 65535)
            except Exception:
                try:
                    self.child.setwinsize(10000, 10000)
                except Exception:
                    pass
            if "prompt" in self.tags:
                prompt = self.tags["prompt"]
            expects = [prompt, pexpect.EOF, pexpect.TIMEOUT]

            output = ''
            if not isinstance(commands, list):
                commands = [commands]
            if not isinstance(expected, list):
                expected = [expected]
            if "screen_length_command" in self.tags:
                commands.insert(0, self.tags["screen_length_command"])
            self.mylog = io.BytesIO()
            self.child.logfile_read = self.mylog
            for c in commands:
                if vars is not None:
                    try:
                        c = c.format(**vars)
                    except KeyError as e:
                        self.output = f"Error: Variable {e} not defined in task or inventory"
                        self.status = 1
                        return self.output
                result = self.child.expect(expects, timeout = timeout)
                self.child.sendline(c)
                if result == 2:
                    break
            if not result == 2:
                result = self.child.expect(expects, timeout = timeout)
            self.child.close()
            output = self._logclean(self.mylog.getvalue().decode(), True)
            if logger:
                logger("output", output)
            if folder != '':
                with open(folder + "/" + self.unique + "_" + now + ".txt", "w") as f:
                    f.write(output)
                    f.close()
            self.output = output
            if result in [0, 1]:
                # lastcommand = commands[-1]
                # if vars is not None:
                    # lastcommand = lastcommand.format(**vars)
                # last_command_index = output.rfind(lastcommand)
                # cleaned_output = output[last_command_index + len(lastcommand):].strip()
                self.result = {}
                for e in expected:
                    if vars is not None:
                        e = e.format(**vars)
                    updatedprompt = re.sub(r'(?<!\\)\$', '', prompt)
                    newpattern = f".*({updatedprompt}).*{e}.*"
                    cleaned_output = output
                    cleaned_output = re.sub(newpattern, '', cleaned_output)
                    if e in cleaned_output:
                        self.result[e] = True
                    else:
                        self.result[e]= False
                self.status = 0
                return self.result
            if result == 2:
                self.result = None
                self.status = 2
                return output
        else:
            self.result = None
            self.output = connect
            self.status = 1
            return connect

    @MethodHook
    def _generate_ssh_sftp_cmd(self):
        cmd = self.protocol
        if self.idletime > 0:
            cmd += " -o ServerAliveInterval=" + str(self.idletime)
        if self.port:
            if self.protocol == "ssh":
                cmd += " -p " + self.port
            elif self.protocol == "sftp":
                cmd += " -P " + self.port
        if self.options:
            opts = self.options
            if self.protocol == "sftp":
                # Strip SSH-only flags that sftp doesn't support
                opts = re.sub(r'(?<!\S)-[XxtTAaNf]\b', '', opts).strip()
            if opts:
                cmd += " " + opts
        if self.jumphost:
            cmd += " " + self.jumphost
        user_host = f"{self.user}@{self.host}" if self.user else self.host
        cmd += f" {user_host}"
        return cmd

    @MethodHook
    def _generate_telnet_cmd(self):
        cmd = f"telnet {self.host}"
        if self.port:
            cmd += f" {self.port}"
        if self.options:
            cmd += f" {self.options}"
        return cmd

    @MethodHook
    def _generate_kube_cmd(self):
        cmd = f"kubectl exec {self.options} {self.host} -it --"
        kube_command = self.tags.get("kube_command", "/bin/bash") if isinstance(self.tags, dict) else "/bin/bash"
        cmd += f" {kube_command}"
        return cmd

    @MethodHook
    def _generate_docker_cmd(self):
        cmd = f"docker {self.options} exec -it {self.host}"
        docker_command = self.tags.get("docker_command", "/bin/bash") if isinstance(self.tags, dict) else "/bin/bash"
        cmd += f" {docker_command}"
        return cmd

    @MethodHook
    def _generate_ssm_cmd(self):
        region = self.tags.get("region", "") if isinstance(self.tags, dict) else ""
        profile = self.tags.get("profile", "") if isinstance(self.tags, dict) else ""
        cmd = f"aws ssm start-session --target {self.host}"
        if region:
            cmd += f" --region {region}"
        if profile:
            cmd += f" --profile {profile}"
        if self.options:
            cmd += f" {self.options}"
        return cmd

    @MethodHook
    def _get_cmd(self):
        if self.protocol in ["ssh", "sftp"]:
            return self._generate_ssh_sftp_cmd()
        elif self.protocol == "telnet":
            return self._generate_telnet_cmd()
        elif self.protocol == "kubectl":
            return self._generate_kube_cmd()
        elif self.protocol == "docker":
            return self._generate_docker_cmd()
        elif self.protocol == "ssm":
            return self._generate_ssm_cmd()
        else:
            printer.error(f"Invalid protocol: {self.protocol}")
            sys.exit(1)

    @MethodHook
    def _connect(self, debug=False, timeout=10, max_attempts=3, logger=None):

        cmd = self._get_cmd()
        passwords = self._passtx(self.password) if self.password and any(self.password) else []
        if self.logs != '':
            self.logfile = self._logfile()
        default_prompt = r'>$|#$|\$$|>.$|#.$|\$.$'
        prompt = self.tags.get("prompt", default_prompt) if isinstance(self.tags, dict) else default_prompt
        password_prompt = '[p|P]assword:|[u|U]sername:' if self.protocol != 'telnet' else '[p|P]assword:'

        expects = {
            "ssh": ['yes/no', 'refused', 'supported', 'Invalid|[u|U]sage: ssh', 'ssh-keygen.*\"', 'timeout|timed.out', 'unavailable', 'closed', password_prompt, prompt, 'suspend', pexpect.EOF, pexpect.TIMEOUT, "No route to host", "resolve hostname", "no matching", "[b|B]ad (owner|permissions)"],
            "sftp": ['yes/no', 'refused', 'supported', 'Invalid|[u|U]sage: sftp', 'ssh-keygen.*\"', 'timeout|timed.out', 'unavailable', 'closed', password_prompt, prompt, 'suspend', pexpect.EOF, pexpect.TIMEOUT, "No route to host", "resolve hostname", "no matching", "[b|B]ad (owner|permissions)"],
            "telnet": ['[u|U]sername:', 'refused', 'supported', 'invalid|unrecognized option', 'ssh-keygen.*\"', 'timeout|timed.out', 'unavailable', 'closed', password_prompt, prompt, 'suspend', pexpect.EOF, pexpect.TIMEOUT, "No route to host", "resolve hostname", "no matching", "[b|B]ad (owner|permissions)"],
            "kubectl": ['[u|U]sername:', '[r|R]efused', '[E|e]rror', 'DEPRECATED', pexpect.TIMEOUT, password_prompt, prompt, pexpect.EOF, "expired|invalid"],
            "docker": ['[u|U]sername:', 'Cannot', '[E|e]rror', 'failed', 'not a docker command', 'unknown', 'unable to resolve', pexpect.TIMEOUT, password_prompt, prompt, pexpect.EOF],
            "ssm": ['[u|U]sername:', 'Cannot', '[E|e]rror', 'failed', 'SessionManagerPlugin', '[u|U]nknown', 'unable to resolve', pexpect.TIMEOUT, password_prompt, prompt, pexpect.EOF]
        }

        error_indices = {
            "ssh": [1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16],
            "sftp": [1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16],
            "telnet": [1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16],
            "kubectl": [1, 2, 3, 4, 8],  # Define error indices for kube
            "docker": [1, 2, 3, 4, 5, 6, 7],  # Define error indices for docker
            "ssm": [1, 2, 3, 4, 5, 6, 7]
        }

        eof_indices = {
            "ssh": [8, 9, 10, 11],
            "sftp": [8, 9, 10, 11],
            "telnet": [8, 9, 10, 11],
            "kubectl": [5, 6, 7],  # Define eof indices for kube
            "docker": [8, 9, 10],  # Define eof indices for docker
            "ssm": [8, 9, 10]
        }

        initial_indices = {
            "ssh": [0],
            "sftp": [0],
            "telnet": [0],
            "kubectl": [0],  # Define special indices for kube
            "docker": [0],  # Define special indices for docker
            "ssm": [0]
        }

        attempts = 1
        while attempts <= max_attempts:
            child = pexpect.spawn(cmd)
            if isinstance(self.tags, dict) and self.tags.get("console"):
                child.sendline()
            if debug:
                if logger:
                    logger("debug", f"Command:\n{cmd}")
                self.mylog = io.BytesIO()
                self.mylog.write(f"[i] [DEBUG] Command:\r\n    {cmd}\r\n".encode())
                child.logfile_read = self.mylog


            endloop = False
            for i in range(len(passwords) if passwords else 1):
                while True:
                    results = child.expect(expects[self.protocol], timeout=timeout)
                    results_value = expects[self.protocol][results]
                    
                    if results in initial_indices[self.protocol]:
                        if self.protocol in ["ssh", "sftp"]:
                            child.sendline('yes')
                        elif self.protocol in ["telnet", "kubectl", "docker", "ssm"]:
                            if self.user:
                                child.sendline(self.user)
                            else:
                                self.missingtext = True
                                break
                    
                    elif results in error_indices[self.protocol]:
                        child.terminate()
                        if results_value == pexpect.TIMEOUT and attempts != max_attempts:
                            attempts += 1
                            endloop = True
                            break
                        else:
                            after = "Connection timeout" if results_value == pexpect.TIMEOUT else child.after.decode()
                            return f"Connection failed code: {results}\n{child.before.decode().lstrip()}{after}{child.readline().decode()}".rstrip()
                    
                    elif results in eof_indices[self.protocol]:
                        if results_value == password_prompt:
                            if passwords:
                                child.sendline(passwords[i])
                            else:
                                self.missingtext = True
                            break
                        elif results_value == "suspend":
                            child.sendline("\r")
                            sleep(2)
                        else:
                            endloop = True
                            child.sendline()
                            break
                    
                if endloop:
                    break
            if results_value == pexpect.TIMEOUT:
                continue
            else:
                break

        if isinstance(self.tags, dict) and self.tags.get("post_connect_commands"):
            cmds = self.tags.get("post_connect_commands")
            commands = [cmds] if isinstance(cmds, str) else cmds
            for command in commands:
                child.sendline(command)
                sleep(1)
        child.readline(0)
        self.child = child
        from pexpect import fdpexpect
        self.raw_child = fdpexpect.fdspawn(self.child.child_fd)
        return True

@ClassHook
class nodes:
    ''' This class generates a nodes object. Contains a list of node class objects and methods to run multiple tasks on nodes simultaneously.

    ### Attributes:  

        - nodelist (list): List of node class objects passed to the init 
                           function.  

        - output   (dict): Dictionary formed by nodes unique as keys, 
                           output of the commands you ran on the node as 
                           value. Created after running methods run or test.  

        - result   (dict): Dictionary formed by nodes unique as keys, value 
                           is True if expected value is found after running 
                           the commands, False if prompt is found before. 
                           Created after running method test.  

        - status   (dict): Dictionary formed by nodes unique as keys, value: 
                           0 if method run or test ended successfully.
                           1 if connection failed.
                           2 if expect timeouts without prompt or EOF.

        - <unique> (obj):  For each item in nodelist, there is an attribute
                           generated with the node unique.
        '''

    def __init__(self, nodes: dict, config = ''):
        ''' 
        ### Parameters:  

            - nodes (dict): Dictionary formed by node information:  
                            Keys: Unique name for each node.  
                            Mandatory Subkeys: host(str).  
                            Optional Subkeys: options(str), logs(str), password(str),
                            port(str), protocol(str), user(str).  
                            For reference on subkeys check node class.

        ### Optional Parameters:  

            - config (obj): Pass the object created with class configfile with key 
                            for decryption and extra configuration if you are using 
                            connection manager.
        '''
        self.nodelist = []
        self.config = config
        for n in nodes:
            this = node(n, **nodes[n], config = config)
            self.nodelist.append(this)
            setattr(self,n,this)

    
    @MethodHook
    def _splitlist(self, lst, n):
        #split a list in lists of n members.
        for i in range(0, len(lst), n):
            yield lst[i:i + n]


    @MethodHook
    def run(self, commands, vars = None,*, folder = None, prompt = None, stdout = None, parallel = 10, timeout = None, on_complete = None, logger = None):
        '''
        Run a command or list of commands on all the nodes in nodelist.


        ### Parameters:  

            - commands (str/list): Commands to run on the nodes. Should be str or 
                                   list of str. You can use variables as {varname}
                                   and defining them in optional parameter vars.

        ### Optional Parameters:  

            - vars  (dict): Dictionary containing the definition of variables for
                            each node, used in commands parameter.
                            Keys should be formed by nodes unique names. Use
                            special key name __global__ for global variables.
                            Subkeys: Variable names.
                            Values: strings.

        ### Optional Named Parameters:  

            - folder   (str): Path where output log should be stored, leave empty 
                              to disable logging.  

            - prompt   (str): Prompt to be expected after a command is finished 
                              running. Usually linux uses  ">" or EOF while routers 
                              use ">" or "#". The default value should work for 
                              most nodes. Change it if your connection need some 
                              special symbol.  

            - stdout  (bool): Set True to send the command output to stdout. 
                              Default False.  

            - parallel (int): Number of nodes to run the commands simultaneously. 
                              Default is 10, if there are more nodes that this 
                              value, nodes are groups in groups with max this 
                              number of members.
            
            - timeout  (int): Time in seconds for expect to wait for prompt/EOF.
                              default 10.

            - on_complete (callable): Optional callback called when each node 
                                      finishes. Receives (unique, output, status).
                                      Called from the node's thread so it must
                                      be thread-safe.

        ###Returns:  

            dict: Dictionary formed by nodes unique as keys, Output of the 
                  commands you ran on the node as value.

        '''
        args = {}
        nodesargs = {}
        args["commands"] = commands
        if folder != None:
            args["folder"] = folder
            Path(folder).mkdir(parents=True, exist_ok=True)
        if prompt != None:
            args["prompt"] = prompt
        if stdout != None and on_complete is None:
            args["stdout"] = stdout
        if timeout != None:
            args["timeout"] = timeout
        output = {}
        status = {}
        tasks = []

        def _run_node(node_obj, node_args, callback):
            """Wrapper that runs a node and fires the callback on completion."""
            node_obj.run(**node_args)
            if callback:
                callback(node_obj.unique, node_obj.output, node_obj.status)

        for n in self.nodelist:
            nodesargs[n.unique] = deepcopy(args)
            if vars != None:
                nodesargs[n.unique]["vars"] = {}
                if "__global__" in vars.keys():
                    nodesargs[n.unique]["vars"].update(vars["__global__"])
                for var_key, var_val in vars.items():
                    if var_key == "__global__":
                        continue
                    try:
                        if re.search(var_key, n.unique, re.IGNORECASE):
                            nodesargs[n.unique]["vars"].update(var_val)
                    except re.error:
                        if var_key == n.unique:
                            nodesargs[n.unique]["vars"].update(var_val)
            
            # Pass the logger to the node
            nodesargs[n.unique]["logger"] = logger

            if on_complete:
                tasks.append(threading.Thread(target=_run_node, args=(n, nodesargs[n.unique], on_complete)))
            else:
                tasks.append(threading.Thread(target=n.run, kwargs=nodesargs[n.unique]))

        taskslist = list(self._splitlist(tasks, parallel))

        for t in taskslist:
            for i in t:
                i.start()
            for i in t:
                i.join()
        for i in self.nodelist:
            output[i.unique] = i.output
            status[i.unique] = i.status
        self.output = output
        self.status = status
        return output

    @MethodHook
    def test(self, commands, expected, vars = None,*, folder = None, prompt = None, parallel = 10, timeout = None, on_complete = None, logger = None):
        '''
        Run a command or list of commands on all the nodes in nodelist, then check if expected value appears on the output after the last command.


        ### Parameters:  

            - commands (str/list): Commands to run on the node. Should be str or 
                                   list of str.  

            - expected (str)     : Expected text to appear after running all the 
                                   commands on the node.

        ### Optional Parameters:  

            - vars  (dict): Dictionary containing the definition of variables for
                            each node, used in commands and expected parameters.
                            Keys should be formed by nodes unique names. Use
                            special key name __global__ for global variables.
                            Subkeys: Variable names.
                            Values: strings.

        ### Optional Named Parameters:  

            - prompt   (str): Prompt to be expected after a command is finished 
                              running. Usually linux uses  ">" or EOF while 
                              routers use ">" or "#". The default value should 
                              work for most nodes. Change it if your connection 
                              need some special symbol.


            - parallel (int): Number of nodes to run the commands simultaneously. 
                              Default is 10, if there are more nodes that this 
                              value, nodes are groups in groups with max this 
                              number of members.

            - timeout  (int): Time in seconds for expect to wait for prompt/EOF.
                              default 10.

            - on_complete (callable): Optional callback called when each node 
                                      finishes. Receives (unique, output, status).
                                      Called from the node's thread so it must
                                      be thread-safe.

        ### Returns:  

            dict: Dictionary formed by nodes unique as keys, value is True if 
                  expected value is found after running the commands, False 
                  if prompt is found before.

        '''
        args = {}
        nodesargs = {}
        args["commands"] = commands
        args["expected"] = expected
        if folder != None:
            args["folder"] = folder
            Path(folder).mkdir(parents=True, exist_ok=True)
        if prompt != None:
            args["prompt"] = prompt
        if timeout != None:
            args["timeout"] = timeout
        output = {}
        result = {}
        status = {}
        tasks = []

        def _test_node(node_obj, node_args, callback):
            """Wrapper that runs a node test and fires the callback on completion."""
            node_obj.test(**node_args)
            if callback:
                callback(node_obj.unique, node_obj.output, node_obj.status, node_obj.result)

        for n in self.nodelist:
            nodesargs[n.unique] = deepcopy(args)
            if vars != None:
                nodesargs[n.unique]["vars"] = {}
                if "__global__" in vars.keys():
                    nodesargs[n.unique]["vars"].update(vars["__global__"])
                for var_key, var_val in vars.items():
                    if var_key == "__global__":
                        continue
                    try:
                        if re.search(var_key, n.unique, re.IGNORECASE):
                            nodesargs[n.unique]["vars"].update(var_val)
                    except re.error:
                        if var_key == n.unique:
                            nodesargs[n.unique]["vars"].update(var_val)
            nodesargs[n.unique]["logger"] = logger
            
            if on_complete:
                tasks.append(threading.Thread(target=_test_node, args=(n, nodesargs[n.unique], on_complete)))
            else:
                tasks.append(threading.Thread(target=n.test, kwargs=nodesargs[n.unique]))

        taskslist = list(self._splitlist(tasks, parallel))
        for t in taskslist:
            for i in t:
                i.start()
            for i in t:
                i.join()
        for i in self.nodelist:
            result[i.unique] = i.result
            output[i.unique] = i.output
            status[i.unique] = i.status
        self.output = output
        self.result = result
        self.status = status
        return result

# script
