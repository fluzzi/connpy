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
from contextlib import contextmanager

@contextmanager
def copilot_terminal_mode():
    import sys, tty, termios
    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
        
        # Primero pasamos a raw mode absoluto para matar ISIG, ICANON, ECHO, etc.
        tty.setraw(fd)
        
        # Luego rehabilitamos OPOST para que rich.Live se dibuje correctamente
        new_settings = termios.tcgetattr(fd)
        new_settings[1] = new_settings[1] | termios.OPOST
        termios.tcsetattr(fd, termios.TCSANOW, new_settings)
        
        yield
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSANOW, old_settings)
        except Exception:
            pass

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
        self.cmd_byte_positions = [(0, None)]

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
        """Remove special ascii characters and process terminal cursor movements to clean logs."""
        from .utils import log_cleaner
        
        if var == False:
            try:
                with open(logfile, "r") as f:
                    t = f.read()
            except:
                return
        else:
            t = logfile
            
        result = log_cleaner(t)

        if var == False:
            try:
                with open(logfile, "w") as f:
                    f.write(result)
            except:
                pass
            return
        else:
            return result

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

        # Always initialize self.mylog to capture terminal context for the AI Copilot
        if not hasattr(self, 'mylog'):
            self.mylog = io.BytesIO()
            
        if not async_mode:
            self.child.logfile_read = self.mylog
            
        # Only start disk-logging tasks if logfile is configured
        if 'logfile' in dir(self):
            if not async_mode:
                # Start the _savelog thread (sync mode)
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
            
            # Reset and track command byte positions for copilot context navigation
            # Each entry is (byte_position, command_text_or_None)
            self.cmd_byte_positions = [(self.mylog.tell() if hasattr(self, 'mylog') else 0, None)]
            
            def _child_read_ready():
                try:
                    # Increase buffer to 64KB for better high-speed handling
                    data = os.read(child_fd, 65536)
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
                        # Build node info from available metadata and ensure values are strings (not bytes)
                        def to_str(val):
                            if isinstance(val, bytes):
                                return val.decode(errors='replace')
                            return str(val) if val is not None else "unknown"
                            
                        node_info = {
                            "name": to_str(getattr(self, 'unique', 'unknown')), 
                            "host": to_str(getattr(self, 'host', 'unknown'))
                        }
                        if isinstance(getattr(self, 'tags', None), dict):
                            node_info["os"] = to_str(self.tags.get("os", "unknown"))
                            node_info["prompt"] = to_str(self.tags.get("prompt", r'>$|#$|\$$|>.$|#.$|\$.$'))
                        
                        # Invoke copilot (async callback handles UI)
                        await copilot_handler(self.mylog.getvalue(), node_info, local_stream, child_fd, self.cmd_byte_positions)
                        continue
                    
                    # Remove any stray \x00 bytes and forward normally
                    clean_data = data.replace(b'\x00', b'')
                    if clean_data:
                        # Track command boundaries when user hits Enter
                        if hasattr(self, 'mylog') and (b'\r' in clean_data or b'\n' in clean_data):
                            self.cmd_byte_positions.append((self.mylog.tell(), None))

                        try:                            os.write(child_fd, clean_data)
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
                    
                    # Batching Optimization: Drain the queue to batch writes during high-volume bursts
                    # Helps the terminal parse ANSI faster and reduces syscalls.
                    chunks = [data]
                    while not child_reader_queue.empty():
                        try:
                            extra = child_reader_queue.get_nowait()
                            if not extra:
                                chunks.append(b'') # Re-put EOF later or handle it
                                break
                            chunks.append(extra)
                        except asyncio.QueueEmpty:
                            break
                    
                    has_eof = chunks[-1] == b''
                    if has_eof:
                        chunks.pop()
                    
                    if chunks:
                        combined_data = b''.join(chunks)
                        if skip_newlines:
                            stripped = combined_data.lstrip(b'\r\n')
                            if stripped:
                                skip_newlines = False
                                combined_data = stripped
                            else:
                                if has_eof: break
                                continue
                                
                        await local_stream.write(combined_data)
                        if hasattr(self, 'mylog'):
                            self.mylog.write(combined_data)
                    
                    if has_eof:
                        break
                        
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
                            # Move heavy log cleaning to a thread to avoid freezing the interaction loop
                            raw_log = self.mylog.getvalue().decode(errors='replace')
                            cleaned_log = await asyncio.to_thread(self._logclean, raw_log, True)
                            with open(self.logfile, "w") as f:
                                f.write(cleaned_log)
                            prev_size = current_size
                        except Exception:
                            pass

            try:
                # We wait for either the user (ingress) or the child (egress) to finish
                tasks = [
                    asyncio.create_task(ingress_task()),
                    asyncio.create_task(egress_task())
                ]
                if self.idletime > 0:
                    tasks.append(asyncio.create_task(keepalive_task()))
                if hasattr(self, 'logfile') and hasattr(self, 'mylog'):
                    tasks.append(asyncio.create_task(savelog_task()))
                
                done, pending = await asyncio.wait(
                    [tasks[0], tasks[1]], 
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # If ingress finished first (user quit), give egress a small window to catch up 
                # on the remaining output in the queue.
                if tasks[0] in done and tasks[1] not in done:
                    try:
                        await asyncio.wait_for(tasks[1], timeout=0.2)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
                
                for t in tasks:
                    if t not in done:
                        t.cancel()
                    
                # Final log sync on thread to avoid losing last lines
                if hasattr(self, 'logfile') and hasattr(self, 'mylog'):
                    try:
                        raw_log = self.mylog.getvalue().decode(errors='replace')
                        cleaned_log = await asyncio.to_thread(self._logclean, raw_log, True)
                        with open(self.logfile, "w") as f:
                            f.write(cleaned_log)
                    except Exception:
                        pass

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
    async def inject_commands(self, commands, child_fd, on_inject=None):
        """
        Inject a list of commands into the node's PTY.
        Handles screen_length_command, history tracking and delays.
        """
        if not commands:
            return

        # 0. Clear line
        os.write(child_fd, b'\x15')
        await asyncio.sleep(0.1)

        # 1. Prepare list (prepend screen_length if exists)
        slc = self.tags.get("screen_length_command") if hasattr(self, 'tags') and isinstance(self.tags, dict) else None
        
        to_send = list(commands)
        if slc and slc not in to_send: # avoid duplicates if already there
             to_send.insert(0, slc)

        # 2. Inject one by one
        for cmd in to_send:
            # Register in node's official history (SKIP if it's the administrative screen length command)
            if cmd != slc and hasattr(self, 'cmd_byte_positions') and self.cmd_byte_positions is not None:
                log_pos = self.mylog.tell() if hasattr(self, 'mylog') else 0
                self.cmd_byte_positions.append((log_pos, cmd))
            
            # Write physically to PTY
            os.write(child_fd, (cmd + "\n").encode())
            
            # Notify (e.g., for gRPC or logs) - SKIP for administrative SLC
            if on_inject and cmd != slc:
                if asyncio.iscoroutinefunction(on_inject):
                    await on_inject(cmd)
                else:
                    on_inject(cmd)
            
            # Delay to avoid overwhelming the router
            await asyncio.sleep(0.8)

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
        return self._copilot_handler(config)

    def _copilot_handler(self, config):
        """Unified copilot handler for local session."""
        from .cli.terminal_ui import CopilotInterface
        from .services.ai_service import AIService
        import asyncio
        import os

        async def handler(buffer, node_info, stream, child_fd, cmd_byte_positions=None):
            try:
                interface = CopilotInterface(
                    config, 
                    history=getattr(stream, 'copilot_history', None),
                    session_state=getattr(stream, 'copilot_state', None)
                )
                # Save history back to stream for persistence in current session
                stream.copilot_history = interface.history
                stream.copilot_state = interface.session_state
                
                ai_service = AIService(config)
                
                async def on_ai_call(active_buffer, question, chunk_callback, merged_node_info):
                    return await ai_service.aask_copilot(
                        active_buffer,
                        question,
                        node_info=merged_node_info,
                        chunk_callback=chunk_callback
                    )
                # Get raw bytes from BytesIO
                raw_bytes = self.mylog.getvalue()
                
                # Detener el lector de la terminal para que prompt_toolkit (en run_session) 
                # tenga control exclusivo del stdin sin interferencias de LocalStream.
                if hasattr(stream, 'stop_reading'):
                    stream.stop_reading()
                elif hasattr(stream, '_loop') and hasattr(stream, 'stdin_fd'):
                    # Fallback si no tiene el método (en LocalStream)
                    stream._loop.remove_reader(stream.stdin_fd)
                
                try:
                    with copilot_terminal_mode():
                        while True:
                            action, commands, custom_cmd = await interface.run_session(
                                raw_bytes=raw_bytes,
                                cmd_byte_positions=self.cmd_byte_positions,
                                node_info=node_info,
                                on_ai_call=on_ai_call
                            )
                            if action == "continue":
                                continue
                            break
                finally:
                    print("\033[2m Returning to session...\033[0m", flush=True)
                    # Reiniciar el lector de la terminal para volver al modo interactivo SSH/Telnet
                    if hasattr(stream, 'start_reading'):
                        stream.start_reading()
                    elif hasattr(stream, '_loop') and hasattr(stream, 'stdin_fd'):
                        stream._loop.add_reader(stream.stdin_fd, stream._read_ready)
                
                if action in ("send_all", "custom"):
                    cmds_to_send = commands if action == "send_all" else custom_cmd
                    await self.inject_commands(cmds_to_send, child_fd)
                else:
                    os.write(child_fd, b'\x15\r')
            except Exception as e:
                import traceback
                print(f"\n[ERROR in Copilot Handler] {e}", flush=True)
                traceback.print_exc()
                os.write(child_fd, b'\x15\r')

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
